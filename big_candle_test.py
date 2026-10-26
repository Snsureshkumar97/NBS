#!/usr/bin/env python3
"""The big candle on paper (big_candle.py; the user, 26 Oct 2026: "yes build it on paper first"): the rule - the day's
first 15-minute candle with a body of at least 1.5 x ATR that closes at a new day high or low, 09:45-14:30, one a day,
stop at its other end, one target at twice the risk; the reading on a closed candle; the recommendation it writes is a
COPY; its paper desk - a book of its own, no listener, never the index's slot - opens only when switched on and
closes at its target; the per-index switch, off by default and saved; the wiring; and PARITY with the study that
measured it (big_candle_study.run, "K1.5 end 2R") on three years of candles."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd

import big_candle as bc
import config
import signal_engine as se
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra if not cond else ''}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def frame(rows, d="2026-10-26", start="09:15"):
    idx = pd.date_range(f"{d} {start}", periods=len(rows), freq="15min", tz="Asia/Kolkata")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


# a quiet day before (every candle 20 points high to low - ATR 20), then today
quiet = frame([(22500, 22510, 22490, 22500)] * 25, d="2026-10-23")
today_rows = [(22500, 22560, 22440, 22450),       # 09:15 - big, but the day's first candle is never used
              (22450, 22470, 22440, 22460),       # 09:30 - small
              (22460, 22530, 22455, 22520),       # 09:45 - big (body 60 >= 1.5 x ~20) but under the day high 22,560
              (22520, 22600, 22515, 22590),       # 10:00 - big and a new day high: THE ONE
              (22590, 22700, 22585, 22690)]       # 10:15 - big again, but one a day
df = pd.concat([quiet, frame(today_rows)])

print("1. THE RULE")
ab = bc._atr_before(df)
fb = bc.first_big(df.loc["2026-10-26"], ab.loc[df.loc["2026-10-26"].index])
check("the first candle and a big one under the day's high are passed over; the 10:00 candle's close at a new high is it",
      fb is not None and fb[0] == df.index[25 + 3] and fb[1] == 1 and fb[2] == 22515.0, fb)
red = pd.concat([quiet, frame([(22500, 22510, 22490, 22500), (22500, 22505, 22420, 22430)])])
fr = bc.first_big(red.loc["2026-10-26"], bc._atr_before(red).loc[red.loc["2026-10-26"].index])
check("a red one closing at a new day low: a put, its stop the candle's high", fr is not None and fr[1] == -1 and fr[2] == 22505.0, fr)
late = pd.concat([quiet, frame([(22500, 22510, 22490, 22500)] * 21 + [(22500, 22580, 22495, 22575)])])
check("a big breakout after the 14:30 close: no trade",
      bc.first_big(late.loc["2026-10-26"], bc._atr_before(late).loc[late.loc["2026-10-26"].index]) is None)
small = pd.concat([quiet, frame([(22500, 22510, 22490, 22500), (22500, 22530, 22495, 22525)])])
check("a body under 1.5 x ATR is not big",
      bc.first_big(small.loc["2026-10-26"], bc._atr_before(small).loc[small.loc["2026-10-26"].index]) is None)
check("the measured settings", config.BIG_CANDLE == {"k": 1.5, "target_r": 2.0, "first_close": (9, 45), "last_close": (14, 30)})

print("2. THE READING ON THE LAST CLOSED CANDLE")
forming = pd.concat([df.iloc[:25 + 4], frame([(22590, 22600, 22585, 22595)], start="10:15")])   # 10:15 forming
ev = bc.evaluate("NIFTY", forming, now=pd.Timestamp("2026-10-26 10:15:40", tz="Asia/Kolkata"))
check("the 10:00 close: a call, stop at the candle's low, fresh in its first two minutes",
      ev["ready"] and ev["side"] == 1 and ev["stop"] == 22515.0 and ev["fresh"], ev)
ev_late = bc.evaluate("NIFTY", forming, now=pd.Timestamp("2026-10-26 10:20", tz="Asia/Kolkata"))
check("five minutes on: still the reading, not fresh", ev_late["side"] == 1 and not ev_late["fresh"])
after = pd.concat([df, frame([(22690, 22700, 22680, 22695)], start="10:30")])
ev2 = bc.evaluate("NIFTY", after, now=pd.Timestamp("2026-10-26 10:30:20", tz="Asia/Kolkata"))
check("the next big close: no trade, and why", ev2["side"] == 0 and "Today's big candle was the 10:15 close" in ev2["why"],
      ev2.get("why"))

print("3. WHAT IT WRITES - A COPY")
chain = {"available": True, "spot": 22590.0, "expiry": "2026-10-28",
         "strikes": [{"strike": float(k), "call_ltp": 120.0 + (22600 - k) * 0.5, "put_ltp": 110.0 + (k - 22600) * 0.5,
                      "call_bid": 119.5, "call_ask": 120.5, "put_bid": 109.5, "put_ask": 110.5} for k in range(22400, 22850, 50)]}
oi = dict(se.compute_option_chain_signal(dict(chain, pcr=1.0, max_pain=22600, top_call_oi_strike=22800,
                                              top_put_oi_strike=22400)), available=True, strikes=chain["strikes"])
tech = {"last_close": 22590.0, "last_rsi": 50.0, "last_atr": 30.0, "ema_fast": 22600.0, "ema_slow": 22600.0, "trend_score": 0,
        "macd_score": 0, "macd_hist": 0.0, "rsi_score": 0, "vwap_score": 0, "di_score": 0, "st_score": 0, "vol_score": 0,
        "plus_di": 20.0, "minus_di": 20.0, "adx": 15.0, "adx_ok": False, "vwap": 22600.0, "vwap_gap": 10.0,
        "last_swing_low": 22560.0, "last_swing_high": 22660.0, "total_score": 0, "max_score": 4}
base = se.build_recommendation("NIFTY", tech, oi, 50)
base["candles"] = forming
before = dict(base)
out = bc.apply(base, ev, "NIFTY")
R = out["spot"] - 22515.0
check("a call at the money, stop at the candle's low, one target at twice the risk (T1-T3 its thirds)",
      out["option_type"] == "CE" and out["index_stop_loss"] == 22515.0 and out["target_basis"] == "rule"
      and out["index_targets"] == [round(out["spot"] + 2 * R * f / 3, 2) for f in (1, 2, 3)],
      (out.get("index_stop_loss"), out.get("index_targets")))
check("its own system and name", out["rule"]["system"] == "big_candle" and out["rule"]["label"] == "Big candle (paper)")
check("the engine's own recommendation is untouched", set(base) == set(before) and all(base[k] is before[k] for k in base)
      and "rule" not in base, [k for k in base if base.get(k) is not before.get(k)])

print("4. THE PAPER DESK - ITS OWN BOOK, NO LISTENER, ONLY WHEN SWITCHED ON")
CLOCK = {"now": dt.datetime(2026, 10, 26, 10, 15, 40, tzinfo=IST)}
tickets.now_ist = lambda: CLOCK["now"]


class Feed:
    email, market, streamer = "bc@example.invalid", "nse_index", None
    def __init__(self):
        self.tickets = tickets.TicketBook(self.email, self.market)


f = Feed()
desk = bc.PaperDesk(f)
check("its own log beside the account's", desk.book.path == os.path.join(os.path.dirname(f.tickets.path), "big_candle_trades.csv")
      and desk.book.path != f.tickets.path)
check("no listener (it can never reach live orders), no re-arm", desk.book.listeners == [] and desk.book.auto_rearm is False)
evs = desk.step("NIFTY", dict(base), on=False)
check("switched off: no ticket", desk.book.books["NIFTY"].trade is None and not evs)
evs = desk.step("NIFTY", dict(base), on=True)
t = desk.book.books["NIFTY"].trade
check("switched on: a paper ticket on the fresh close - its system, one stop and one target, a plain exit",
      t is not None and t["status"] == "OPEN" and t["system"] == "big_candle" and t["plain_exit"]
      and not tickets.rules_exit(t) and tickets.near_plan(t) == [], t and t.get("status"))
check("the account's own book is untouched - the index's slot stays free", f.tickets.books["NIFTY"].trade is None)
CLOCK["now"] = dt.datetime(2026, 10, 26, 10, 40, tzinfo=IST)
desk.book.tick_price("NIFTY", t["premium_targets"][2] + 0.5)
t = desk.book.books["NIFTY"].trade
check("it closes at its one target", t["status"] != "OPEN" and "T3 hit" in t["status"], t["status"])
rows = open(desk.book.path).read()
check("written to its own log, not the account's", "big_candle" in rows and (
    not os.path.exists(f.tickets.path) or "big_candle" not in open(f.tickets.path).read()))
pub = desk.public()
check("the Dashboard's figures: the switch and reading per index, and the record", pub["record"]["closed"] == 1
      and pub["record"]["wins"] == 1 and pub["by_index"]["NIFTY"]["on"] is True and "ticket" in pub["by_index"]["NIFTY"], pub["record"])

print("5. THE SWITCH - PER INDEX, OFF UNLESS TURNED ON, SAVED")
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
b = tickets.TicketBook(market="nse_index", path=path)
check("off by default on every index", all(b.big_candle_for(k) == "off" for k in config.instruments_in("nse_index")))
b.configure(bigc="on", bigc_index="NIFTY")
b.configure(bigc="maybe", bigc_index="SENSEX")
b.configure(bigc="on", bigc_index="BTC")
check("one index on; an unknown value or index changes nothing", b.big_candle_for("NIFTY") == "on"
      and b.big_candle_for("SENSEX") == "off")
check("saved across a restart", tickets.TicketBook(market="nse_index", path=path).big_candle_for("NIFTY") == "on")
ses = b.session()
check("the page gets each index's switch and the choices", ses["bigc_by"]["NIFTY"] == "on" and ses["bigc_choices"] == ["off", "on"])
check("not offered on crypto", tickets.TicketBook(market="crypto", path=os.path.join(tempfile.mkdtemp(), "t.csv")).session()["bigc_choices"] == [])

print("6. WIRED IN")
src = open(os.path.join(HERE, "feeds.py")).read()
web = open(os.path.join(HERE, "web_server.py")).read()
check("the feed: a paper desk per Indian account, a step per reading, priced on the tick, squared at the bell, published",
      "self.bc = big_candle.PaperDesk(self)" in src and 'self.bc.step(name, rec, self.tickets.big_candle_for(name) == "on")' in src
      and "self.bc.price_tick()" in src and "self.bc.book.close_all_at_bell()" in src and 'snap["big_candle"]' in src)
check("the page: the switch in Trade settings, the Dashboard section, the state", 'select data-bigc=' in web
      and "function bigCandleDash(s, order)" in web and '"big_candle": snap.get("big_candle")' in web
      # its own slot on the home pane, outside #kdash (hidden on a phone)
      and '<div class="kdash" id="kdash"></div>\n  <div class="bcdash" id="bcdash"></div>' in web
      and 'bigc=(form.get("bigc") or None)' in web)

print("7. PARITY WITH THE STUDY THAT MEASURED IT (three years)")
import backtest_intraday as bt
import big_candle_study as bcs
v = ("K1.5 end 2R", 1.5, True, "end", "2R")
for key in ("NIFTY", "SENSEX"):
    hist = bt.fetch_history(key, years=3, use_cache=True)
    live = {(t + bc.BAR, "CE" if s > 0 else "PE") for t, s, _ in bc.signals(hist[["Open", "High", "Low", "Close"]])}
    study = {(r["when"], r["side"]) for r in bcs.run(key, v)}
    check(f"{key}: every one of the study's {len(study)} trades is a live-rule entry, same moment and side", study <= live,
          sorted(study - live)[:3])
    check(f"{key}: and the live rule adds none of its own ({len(live)}; the study skips only days it could not price)",
          len(live - study) <= 0.03 * len(live), sorted(live - study)[:5])

print()
print("BIG CANDLE TEST PASSED" if not fails else f"BIG CANDLE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
