#!/usr/bin/env python3
"""cfd_rules.py - the live votes must be the ones cfd_vote_search.py scored, bar for bar, and
a rule's reading must become exactly the ticket that was tested. Needs the Exness history cache
(exness_data.py) for part 1; skipped there without it."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["ENABLE_CRYPTO"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import cfd_rules
import config

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE LIVE VOTES ARE THE SEARCH'S VOTES - from 30 days of candles, as live, at random bars")
try:
    import exness_data as ed
    import cfd_vote_search as vs
    have = all(os.path.exists(os.path.join(ed._dir(), f)) for f in ("BTCUSD_2026_08_15m.csv", "XAUUSD_2026_08_15m.csv"))
except Exception:
    have = False
if not have:
    print("  (skipped: no Exness history cache on this machine)")
else:
    for sym, key in (("BTCUSD", "BTC"), ("XAUUSD", "GOLD")):
        full = ed.load(sym)
        V, F = vs.votes_and_filters(full, key, [None] * len(full))
        ref = dict(V, **F)
        win = 96 * cfd_rules.HISTORY_DAYS
        rng = np.random.default_rng(7)
        bars = rng.choice(np.arange(win, len(full) - 1), 300, replace=False)
        names = sorted({n for p in config.CFD_RULES.values() for n in p["votes"] + p["filters"]})
        bad = {n: 0 for n in names}
        sides_bad = 0
        plan = config.CFD_RULES[key]
        for i in bars:
            df = full.iloc[i - win + 1:i + 1]
            a = cfd_rules.compute(df)
            for n in names:
                if (int(a[n][-1]) if a[n].dtype != bool else bool(a[n][-1])) != \
                   (int(ref[n][i]) if ref[n].dtype != bool else bool(ref[n][i])):
                    bad[n] += 1
            # as live: the next candle has started (it is in the data), a minute after the close
            ev = cfd_rules.evaluate(key, full.iloc[i - win + 1:i + 2], now=df.index[-1] + cfd_rules.BAR + pd.Timedelta(minutes=1))
            vs_ = [ref[v][i] for v in plan["votes"]]
            want = vs_[0] if vs_[0] != 0 and all(x == vs_[0] for x in vs_) and all(ref[f][i] for f in plan["filters"]) else 0
            sides_bad += int(ev["side"] != want)
        check(f"{key}: every vote and filter on 300 random bars, 30 days of history vs 3 years", sum(bad.values()) == 0, bad)
        whole = cfd_rules.compute(full)
        diff = {n: int(np.sum(np.asarray(whole[n]) != np.asarray(ref[n]))) for n in names}
        check(f"{key}: and on EVERY one of the {len(full):,} bars of the 3 years (whole history, same formulas)",
              sum(diff.values()) == 0, diff)
        check(f"{key}: the rule's buy/sell/none matches the search on all 300", sides_bad == 0, sides_bad)

print("2. ONLY CLOSED CANDLES - THE FORMING ONE IS NEVER READ")
idx = pd.date_range("2026-10-01 00:00", periods=400, freq="15min", tz="UTC")
rng = np.random.default_rng(1)
c = 84000 + np.cumsum(rng.normal(0, 30, len(idx)))
df = pd.DataFrame({"Open": c - 5, "High": c + 40, "Low": c - 40, "Close": c, "Volume": 100.0}, index=idx)
now = idx[-1] + pd.Timedelta(minutes=7)                  # the last candle is still forming
ev = cfd_rules.evaluate("BTC", df, now=now)
check("the reading is of the last CLOSED candle (the one before the forming one)",
      ev["ready"] and ev["bar_close"] == (idx[-2] + cfd_rules.BAR).isoformat(), ev.get("bar_close"))
df2 = df.copy(); df2.iloc[-1, df2.columns.get_loc("Close")] += 5000      # the forming candle jumps
check("...so a jump in the forming candle changes nothing", cfd_rules.evaluate("BTC", df2, now=now)["side"] == ev["side"])
check("7 minutes after a close: past the 120-second entry window", ev["fresh"] is False)
ev2 = cfd_rules.evaluate("BTC", df, now=idx[-1] + pd.Timedelta(seconds=60))
check("60 seconds after a close: fresh", ev2["fresh"] is True)
check("no rule for an Indian index", cfd_rules.evaluate("NIFTY", df, now=now) is None)
stale = cfd_rules.evaluate("BTC", df, now=idx[-1] + cfd_rules.BAR + pd.Timedelta(seconds=20))
check("candles fetched before the last one closed (no newer candle yet): it WAITS rather than read half a candle",
      stale["ready"] is False and "waiting" in stale["why"], stale)
check("fewer than 250 closed candles: not ready", cfd_rules.evaluate("BTC", df.iloc[:100], now=idx[99] + pd.Timedelta(minutes=1))["ready"] is False)

print("3. A READING BECOMES THE TESTED TICKET: STOP 3 x ATR, ONE TARGET 0.75 x THE STOP")
ev3 = {"ready": True, "votes": {"candle": -1, "ha": -1}, "filters": {"vol_rising": True}, "side": -1, "atr": 100.0,
       "close": 84000.0, "bar_close": "x", "fresh": True}
rec = cfd_rules.apply({"index": "BTC", "spot": 84000.0}, ev3, "BTC")
check("SELL: stop 84,000 + 3 x 100 = 84,300; target 84,000 - 0.75 x 300 = 83,775 (T1/T2 waypoints on the way)",
      rec["option_type"] == "PE" and rec["index_stop_loss"] == 84300.0 and rec["index_targets"] == [83925.0, 83850.0, 83775.0]
      and rec["risk_points"] == 300.0 and rec["target_basis"] == "rule", (rec["index_stop_loss"], rec["index_targets"]))
check("the Signal card gets the votes and filters by name", [v["name"] for v in rec["rule"]["votes"]] == ["Candle colour", "Heikin-Ashi"]
      and rec["rule"]["filters"][0]["name"] == "Volume rising" and rec["rule"]["filters"][0]["ok"] is True)
rec0 = cfd_rules.apply({"index": "BTC", "spot": 84000.0}, dict(ev3, side=0, votes={"candle": 1, "ha": -1}), "BTC")
check("votes split -> no trade, no levels", rec0["bias"] == "NEUTRAL" and rec0["index_targets"] == [None] * 3
      and rec0["index_stop_loss"] is None)
check("the chosen rules are the ones the user picked (accuracy-first, both markets)",
      config.CFD_RULES["BTC"]["votes"] == ["candle", "ha"] and config.CFD_RULES["BTC"]["filters"] == ["vol_rising"]
      and config.CFD_RULES["GOLD"]["votes"] == ["d1_trend", "h1_trend", "roc12"]
      and config.CFD_RULES["GOLD"]["filters"] == ["adx_rising", "vol_rising"]
      and all(p["stop_atr"] == 3.0 and p["target_r"] == 0.75 for p in config.CFD_RULES.values()))

print("4. THE TICKET ENGINE TRADES A RULE EXACTLY AS TESTED")
import datetime as dt
import tickets
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_now = {"t": dt.datetime(2026, 10, 5, 10, 0, 30, tzinfo=IST)}
tickets.now_ist = lambda: _now["t"]
d = tempfile.mkdtemp()
bk = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d, "t.csv"))
bk.lots = 0.1
def reading(side=1, fresh=True, bar="2026-10-05T04:30:00+00:00", spot=84000.0):
    ev = {"ready": True, "votes": {"candle": side, "ha": side}, "filters": {"vol_rising": True}, "side": side,
          "atr": 100.0, "close": spot, "bar_close": bar, "fresh": fresh}
    r = cfd_rules.apply({"index": "BTC", "spot": spot, "cfd_spread": 10.0, "quote_age_s": 1.0}, ev, "BTC")
    return r
evs = bk.update("BTC", reading())
t = bk.books["BTC"].trade
check("a fresh BUY reading opens a ticket: stop 83,700, target 84,225, one target (plain), exit at T3, rule-marked",
      any(e["kind"] == "opened" for e in evs) and t["index_sl"] == 83700.0 and t["index_targets"][2] == 84225.0
      and t["plain_exit"] and t["exit_at"] == "T3" and t["rule_strategy"] and t["cfd"], (t and (t["index_sl"], t["index_targets"])))
was = getattr(tickets.config, "EARLY_EXIT_ON_REVERSAL", False)
was_s = getattr(tickets.config, "SIGNAL_CONFIRM_SECONDS", 0)
tickets.config.EARLY_EXIT_ON_REVERSAL, tickets.config.SIGNAL_CONFIRM_SECONDS = True, 60
bk.books["BTC"].confirm_dir, bk.books["BTC"].confirm_streak, bk.books["BTC"].confirm_since = ("BEARISH", "PE"), 99, 1.0   # held since long ago
bk.tick_price("BTC", 84050.0)
check("no reversal exit on a rule's ticket (the test had none), with the reversal exit armed", t["status"] == "OPEN", t["status"])
tickets.config.EARLY_EXIT_ON_REVERSAL, tickets.config.SIGNAL_CONFIRM_SECONDS = was, was_s
bk.tick_price("BTC", 84160.0)                       # past T1 84,075 and T2 84,150, short of the target 84,225
check("T1/T2 reached: marked, the stop has NOT moved", t["hit"]["T1"] and t["hit"]["T2"] and t["index_sl"] == 83700.0)
evs = bk.tick_price("BTC", 84230.0)
check("the target: closed as a full target", "T3 hit" in t["status"], t["status"])
was_cd = tickets.config.REENTRY_COOLDOWN_MIN
tickets.config.REENTRY_COOLDOWN_MIN = 0                    # so only the one-decision-per-close guard can stop it
evs = bk.update("BTC", reading())
check("no second ticket on the SAME close (even with no cooldown)", bk.books["BTC"].trade is None
      or bk.books["BTC"].trade["status"] != "OPEN")
check("...and the card says the close is decided", bk.books["BTC"].wait_reason[1] == "NEXT CLOSE", bk.books["BTC"].wait_reason)
for g in ("_same_direction_hold", "_regime_hold", "_reward_hold", "_spread_hold", "_strike_taken", "_closed_hold"):
    setattr(bk, g, lambda *a, **k: None)                    # every engine gate open
was_rm = getattr(tickets.config, "REARM_MIN_SECONDS", 60)
tickets.config.REARM_MIN_SECONDS = 0
bk.books["BTC"].last_bias_signature = ("BULLISH", "CE")
check("a rule's reading never re-arms after a target, whatever the engine's gates say",
      bk._rearm(bk.books["BTC"], reading(bar="2026-10-05T09:00:00+00:00")) is False)
tickets.config.REARM_MIN_SECONDS, tickets.config.REENTRY_COOLDOWN_MIN = was_rm, was_cd
for g in ("_same_direction_hold", "_regime_hold", "_reward_hold", "_spread_hold", "_strike_taken", "_closed_hold"):
    delattr(bk, g)
_now["t"] += dt.timedelta(minutes=15)
bk.update("BTC", reading(bar="2026-10-05T04:45:00+00:00"))
check("the next close within 20 minutes of the exit: COOLDOWN", bk.books["BTC"].wait_reason[1] == "COOLDOWN"
      and bk.books["BTC"].trade.get("status") != "OPEN", bk.books["BTC"].wait_reason)
_now["t"] += dt.timedelta(minutes=15)
bk.update("BTC", reading(bar="2026-10-05T05:00:00+00:00", fresh=False))
check("a close older than the 2-minute window: waits for the next close", bk.books["BTC"].wait_reason[1] == "NEXT CLOSE",
      bk.books["BTC"].wait_reason)
evs = bk.update("BTC", reading(side=-1, bar="2026-10-05T05:15:00+00:00"))
t2 = bk.books["BTC"].trade
check("a fresh SELL on a new close opens: stop 84,300, target 83,775", t2["status"] == "OPEN" and t2["option_type"] == "PE"
      and t2["index_sl"] == 84300.0 and t2["index_targets"][2] == 83775.0)
evs = bk.update("BTC", reading(side=1, bar="2026-10-05T05:30:00+00:00"))
check("a position open: the next BUY close changes nothing", bk.books["BTC"].trade is t2 and t2["status"] == "OPEN"
      and bk.books["BTC"].wait_reason[1] == "POSITION OPEN")
bk.update("BTC", cfd_rules.apply({"index": "BTC", "spot": 84000.0, "quote_age_s": 1.0},
                                 {"ready": True, "votes": {"candle": 1, "ha": -1}, "filters": {"vol_rising": True},
                                  "side": 0, "atr": 100.0, "bar_close": "x", "fresh": True}, "BTC"))
check("votes split: no trade (and the open one is left to its own exit)", bk.books["BTC"].trade is t2)

print("5. THE SIGNAL CARD - THE RULE'S OWN VOTES, IN WORDS")
import explain
w = explain.explain(reading())
check("rows are the rule's votes and filter, no ADX gate", [v["name"] for v in w["votes"]] == ["Candle colour", "Heikin-Ashi",
      "Volume rising"] and w["gate"] is None and w["votes"][0]["text"].startswith("The last 15-minute candle closed above"))
check("the verdict names the decision and the exit", "BUY" in w["verdict"] and "one target at 0.75" in w["verdict"])
check("the levels say where the stop and the one target are", w["levels"][0].startswith("STOP 83,700")
      and "TARGET 84,225" in w["levels"][1], w["levels"])
import feeds
pub = feeds._public(dict(reading(), technical={}, trend={}), "BTC")
check("the page gets the rule, the levels, exit at T3", pub["rule"]["label"] == "Candle + Heikin-Ashi rule"
      and pub["exit_at"] == "T3" and pub["stop"] == 83700.0)
nb = tickets.TicketBook(market="nse_index")
check("an Indian reading has no rule and goes the engine's way", nb.books["NIFTY"] is not None and config.cfd_rule("NIFTY") is None)

print()
if fails:
    print(f"CFD RULES TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("CFD RULES TEST PASSED")
