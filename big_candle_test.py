#!/usr/bin/env python3
"""The big candle in the rules (big_candle.py; the user, 26 Oct 2026: "add the big candle in our rules with live
orders"): the rule - the day's first 15-minute candle with a body of at least 1.5 x ATR that closes at a new day high or
low, 09:45-14:30, one a day, stop at its other end, one target at twice the risk; the reading on a closed candle; the
recommendation it writes is a COPY; one of the book's OWN tickets (TicketBook.consider_big_candle) - only switched on,
fresh and with nothing open, heard by the book's listeners (live orders), kept out of the rules' own spacing, cooldown,
re-arm and restart memory; the per-index switch, off by default and saved; the seller limit gone; the wiring; and
PARITY with the study that measured it (big_candle_study.run, "K1.5 end 2R") on three years of candles."""
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
check("its own system and name", out["rule"]["system"] == "big_candle" and out["rule"]["label"] == "Big candle")
check("the engine's own recommendation is untouched", set(base) == set(before) and all(base[k] is before[k] for k in base)
      and "rule" not in base, [k for k in base if base.get(k) is not before.get(k)])

print("4. ONE OF THE BOOK'S OWN TICKETS - LIVE ORDERS FOLLOW IT; THE RULES' OWN MEMORY UNTOUCHED")
CLOCK = {"now": dt.datetime(2026, 10, 26, 10, 15, 40, tzinfo=IST)}
tickets.now_ist = lambda: CLOCK["now"]
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
nb = tickets.TicketBook(market="nse_index", path=path)
heard = []
nb.listeners.append(lambda kind, trade: heard.append((kind, trade.get("system"))))   # how live orders follow a ticket
bcr = bc.apply(dict(base), ev, "NIFTY")
check("switched off: nothing", nb.consider_big_candle("NIFTY", bcr) == [] and nb.books["NIFTY"].trade is None)
nb.configure(bigc="on", bigc_index="NIFTY")
nb.books["NIFTY"].wait_reason = ("neutral", "NO SIGNAL", "the rules' own hold")
stale = bc.apply(dict(base), ev_late, "NIFTY")
check("on, but the close is not fresh: nothing, and the rules' own hold is left as it was",
      nb.consider_big_candle("NIFTY", stale) == [] and nb.books["NIFTY"].wait_reason[2] == "the rules' own hold")
evs = nb.consider_big_candle("NIFTY", bcr)
t = nb.books["NIFTY"].trade
check("on and fresh with nothing open: a ticket in the book itself - its system, one stop and one target",
      evs and evs[-1]["kind"] == "opened" and t["status"] == "OPEN" and t["system"] == "big_candle" and t["plain_exit"]
      and not tickets.rules_exit(t) and tickets.near_plan(t) == [], evs)
check("the book's listeners hear it open - live orders place it where the index's live orders are on",
      heard == [("opened", "big_candle")], heard)
bk = nb.books["NIFTY"]
check("it is not the rules' last ticket: their spacing and same-way memory are untouched",
      bk.last_ticket_at is None and bk.last_bias_signature is None, (bk.last_ticket_at, bk.last_bias_signature))
check("a second fresh big candle while it is open: nothing - one position per index",
      nb.consider_big_candle("NIFTY", bcr) == [])
CLOCK["now"] = dt.datetime(2026, 10, 26, 10, 40, tzinfo=IST)
tgt = t["premium_targets"][2] + 0.5
hot = dict(base["option_chain"], strikes=[dict(x, call_ltp=tgt) if x["strike"] == t["strike"] else x
                                           for x in base["option_chain"]["strikes"]])
rules_rec = dict(base, bias="BULLISH", option_type="CE", option_chain=hot)
asked = []
nb._rearm = lambda book, rec: asked.append(book.name) or False      # would a natural close ask to re-arm?
nb.update("NIFTY", rules_rec)                                       # closes it at its target, through the book's own check
t2 = nb.books["NIFTY"].trade
del nb._rearm
check("closed at its one target - and a re-arm into the rules is never even asked for",
      t["status"] != "OPEN" and "T3 hit" in t["status"] and t2 is t and asked == [], (t["status"], asked))
check("its close is not the rules' cooldown clock", bk.last_close_at is None, bk.last_close_at)
check("it is in the account's own log, marked as the big candle's", "big_candle" in open(path).read())
check("after a restart the rules' memory still leaves it out",
      tickets.TicketBook(market="nse_index", path=path).books["NIFTY"].last_ticket_at is None)
sm = bc.summary({"NIFTY": {"ready": True, "side": 0, "why": "x", "first": {"at": "10:15", "side": 1}}}, nb)
check("the home pane's figures: the switch and reading per index, and its own record", sm["record"]["closed"] == 1
      and sm["record"]["wins"] == 1 and sm["by_index"]["NIFTY"]["on"] is True and sm["by_index"]["SENSEX"]["on"] is False,
      sm["record"])
x = __import__("explain").explain(bcr)
check("the Signal card's words", "The day's first big 15-minute candle closed at a new day high: BUY CE" in x["verdict"]
      and x["levels"][0].startswith("STOP 22,515") and "the big candle's low" in x["levels"][0], x["verdict"])

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
check("the feed: on an index switched on, every reading offers the big candle to the book itself, and publishes it",
      'self.tickets.big_candle_for(name) == "on"' in src and "self.tickets.consider_big_candle(" in src
      and "big_candle.apply(rec, bev, name, avoid_strikes=self._avoid_strikes(name))" in src
      and "big_candle.summary(self.bc_readings, self.tickets)" in src and "PaperDesk" not in src)
check("the page: the switch in Trade settings, the Dashboard section, the state", 'select data-bigc=' in web
      and "function bigCandleDash(s, order)" in web and '"big_candle": snap.get("big_candle")' in web
      # its own slot on the home pane, outside #kdash (hidden on a phone)
      and '<div class="kdash" id="kdash"></div>\n  <div class="bcdash" id="bcdash"></div>' in web
      and 'title="Taken by the big candle">BC</span>' in web)
check("the seller limit is gone from Trade settings (the user, 26 Oct 2026)", "data-sroom" not in web and "sroom" not in web
      and not hasattr(tickets.TicketBook, "sellers_room_for") and not hasattr(config, "SELLERS_ROOM_CHOICES")
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
