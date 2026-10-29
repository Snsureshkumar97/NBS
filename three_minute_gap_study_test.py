#!/usr/bin/env python3
"""three_minute_gap_study on made-up candles: the day's picks (gap up / down / flat, the shipped 1% stock-gap
filter), the 3-minute close entry, the stops and target, the 15:00 and 15:15 cut-offs, the charges - and a random
walk makes nothing before costs (no fill flaw). No network."""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import three_minute_gap_study as tm

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


GAPS = {"A": 2.5, "B": 1.4, "C": 0.6, "D": 0.1, "E": -0.2, "F": -0.5, "G": -1.2, "H": -3.0, "I": 0.0}

print("1. THE DAY'S PICKS")
k, ch = tm.picks(GAPS, 0.5, shipped=False)
check("gap up: short the top two", k == "GAP_UP" and ch == [("A", 2.5, "SHORT"), ("B", 1.4, "SHORT")], ch)
k, ch = tm.picks(GAPS, -0.5, shipped=False)
check("gap down: buy the bottom two", k == "GAP_DOWN" and ch == [("H", -3.0, "LONG"), ("G", -1.2, "LONG")], ch)
k, ch = tm.picks(GAPS, 0.1, shipped=False)
check("flat: buy the bottom one, short the top one", k == "FLAT" and ch == [("H", -3.0, "LONG"), ("A", 2.5, "SHORT")], ch)
g2 = dict(GAPS, B=0.7)
check("as shipped, a pick under 1% the trade's way is dropped", tm.picks(g2, 0.5)[1] == [("A", 2.5, "SHORT")]
      and tm.picks(g2, 0.5, shipped=False)[1][1][0] == "B")
check("...and one over 10%", tm.picks(dict(GAPS, A=12.0), 0.5)[1] == [("B", 1.4, "SHORT")])
check("the threshold is 0.2% either way", tm.picks(GAPS, 0.2)[0] == "FLAT" and tm.picks(GAPS, -0.21)[0] == "GAP_DOWN")


def day(price=100.0):
    m = np.arange(555, 930)
    p = np.full(len(m), price)
    return m, p.copy(), p.copy(), p.copy(), p.copy()


def at(m, hhmm):
    return int(np.nonzero(m == (hhmm // 100) * 60 + hhmm % 100)[0][0])


def ref(bars, hi=100.4, lo=99.8):
    m, o, h, l, c = bars
    i = at(m, 916)
    h[i], l[i] = hi, lo
    return bars


print("2. ENTRY ON A 3-MINUTE CLOSE")
b = ref(day())
m, o, h, l, c = b
i = at(m, 1002)                                 # the 10:00-10:03 candle; its last minute closes above the high
h[i] = 100.6; c[i] = 100.5; o[i + 1] = 100.55
t = tm.simulate(*b, "LONG")
check("a close above the reference high: bought at the next minute's open", t and t["entry"] == 100.55
      and m[t["i"]] == 603, t)
check("...stop at the reference low, target 1% off the candle's close", t["sl"] == 99.8 and t["tg"] == round(100.5 * 1.01, 2))
check("...nothing touched: out at 15:15's open", t["how"] == "SQUARE_OFF" and t["exit"] == 100.0, t)
b = ref(day()); m, o, h, l, c = b
h[at(m, 1002)] = 101.0                          # a wick only
check("a wick through the high is no entry", tm.simulate(*b, "LONG") is None)
check("a short wants a close below the low", tm.simulate(*b, "SHORT") is None)
b = ref(day()); m, o, h, l, c = b
c[at(m, 917)] = 100.5; o[at(m, 918)] = 100.5   # inside the reference candle: not a breakout
check("the reference candle itself never triggers", tm.simulate(*b, "LONG") is None)
b = ref(day()); m, o, h, l, c = b
c[at(m, 1459)] = 100.5                          # the 14:57-15:00 candle, seen at 15:00
check("no entry from 15:00", tm.simulate(*b, "LONG") is None)
b = ref(day()); m, o, h, l, c = b
c[at(m, 1456)] = 100.5                          # 14:54-14:57, seen at 14:57
check("...but 14:57 is still in", tm.simulate(*b, "LONG") is not None)

print("3. STOPS AND TARGET")
def trade(after):
    b = ref(day()); m, o, h, l, c = b
    i = at(m, 1001)
    c[i] = 100.5
    o[i + 1:] = 100.5; h[i + 1:] = 100.5; l[i + 1:] = 100.5; c[i + 1:] = 100.5
    after(m, o, h, l, c, i + 2)
    return tm.simulate(*b, "LONG")
t = trade(lambda m, o, h, l, c, k: h.__setitem__(k, 101.6))
check("target touched: out at the target", t["how"] == "TARGET" and t["exit"] == round(100.5 * 1.01, 2), t)
t = trade(lambda m, o, h, l, c, k: l.__setitem__(k, 99.5))
check("stop touched: out at the stop", t["how"] == "STOP" and t["exit"] == 99.8, t)
t = trade(lambda m, o, h, l, c, k: (o.__setitem__(k, 99.0), l.__setitem__(k, 99.0)))
check("a minute opening below the stop: out at that open, not the stop", t["how"] == "STOP" and t["exit"] == 99.0, t)
t = trade(lambda m, o, h, l, c, k: (h.__setitem__(k, 101.6), l.__setitem__(k, 99.5)))
check("stop and target in one minute: the stop", t["how"] == "STOP" and t["both"], t)
b = ref(day(), hi=101.5, lo=100.0); m, o, h, l, c = b
c[at(m, 1002)] = 101.6
t = tm.simulate(*b, "LONG")
check("a reference candle wider than 1%: the stop is 1% off the entry signal", t["large"] and t["sl"] == round(101.6 * 0.99, 2), t)

print("4. CHARGES")
c0 = tm.charges(500.0, 505.0, 200, "LONG", 0.0)
buy, sell = 100000.0, 101000.0
want = 40 + 0.00025 * sell + 0.0000297 * (buy + sell) + 1e-6 * (buy + sell) + 0.00003 * buy \
    + 0.18 * (40 + 0.0000297 * (buy + sell) + 1e-6 * (buy + sell))
check("brokerage 20 an order, STT on the sell, exchange, SEBI, stamp on the buy, GST", abs(c0 - want) < 1e-6, (c0, want))
check("a short pays STT on its entry (the sell)", abs(tm.charges(505.0, 500.0, 200, "SHORT", 0.0) - want) < 1e-6)
check("a small order pays 0.03%, not 20", tm.charges(100.0, 100.0, 10, "LONG", 0.0) < 2)
r = tm.book({"side": "SHORT", "entry": 500.0, "exit": 495.0})
check("booked per 1 lakh: 200 shares, a short makes the fall", r["gross"] == 1000.0 and r["net"] < 1000.0, r)

print("5. A RANDOM WALK MAKES NOTHING BEFORE COSTS")
rng = np.random.default_rng(7)
gross = []
for _ in range(5000):
    m = np.arange(555, 930)
    steps = rng.normal(0, 0.0008, len(m))
    c = 100 * np.exp(np.cumsum(steps))
    o = np.concatenate(([100.0], c[:-1]))
    wig = np.abs(rng.normal(0, 0.0004, len(m))) * c
    h, l = np.maximum(o, c) + wig, np.minimum(o, c) - wig
    side = "LONG" if rng.random() < 0.5 else "SHORT"
    t = tm.simulate(m, o, h, l, c, side)
    if t:
        gross.append((t["exit"] - t["entry"]) / t["entry"] * (1 if side == "LONG" else -1))
g = np.array(gross)
se = g.std() / np.sqrt(len(g))
check(f"{len(g)} trades on a coin-flip market: average within 3 standard errors of zero", abs(g.mean()) < 3 * se,
      f"{100 * g.mean():+.4f}% (se {100 * se:.4f}%)")

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
