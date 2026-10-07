#!/usr/bin/env python3
"""scalp10_study.py's own pieces, on made-up candles: the index level that makes an option worth +10, the exit walk
(the stop before the target inside one candle, the optimistic reading kept apart), the charges at big lots with an order
sliced at the freeze quantity, and each momentum rule."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regime_study as rs
import scalp10_study as sc

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. THE PREMIUM TARGET")
K, T, sig = 25000, 3 / 365, 0.13
for call in (True, False):
    p0 = rs.bs(25000, K, T, sig, call)
    S = sc.solve_spot(p0 + 10, K, T, sig, call, 25000 * 0.85, 25000 * 1.15)
    check(f"{'a call' if call else 'a put'}: the solved level makes the option worth exactly 10 more",
          S is not None and abs(rs.bs(S, K, T, sig, call) - (p0 + 10)) < 1e-6 and ((S > 25000) if call else (S < 25000)),
          (round(p0, 2), round(S, 2) if S else None))
check("an unreachable premium gives None", sc.solve_spot(1e6, K, T, sig, True, 21250, 28750) is None)

print("2. THE EXIT WALK")
A = {"hi": np.array([100, 103, 112, 120.]), "lo": np.array([99, 95, 101, 110.]), "cl": np.array([100, 101, 111, 118.]),
     "end": np.array([False, False, False, True])}
A2 = dict(A, lo=np.array([99, 95, 101, 110.]), hi=np.array([100, 111, 112, 120.]))
cons, opt, amb = sc.walk(A2, 0, "CE", 96.0, lambda j: 110.0)
check("a candle reaching both the stop and the target is the stop, the target only in the optimistic reading",
      cons == ([(96.0, 1, 1.0)], "stop") and opt == ([(110.0, 1, 1.0)], "target") and amb is True, (cons, opt, amb))
cons, opt, amb = sc.walk(A, 0, "CE", 90.0, lambda j: 110.0)
check("the target alone: out at the target", cons == ([(110.0, 2, 1.0)], "target") and not amb, cons)
cons, opt, amb = sc.walk(A, 0, "CE", 90.0, lambda j: 500.0)
check("neither: out at the day's last close", cons == ([(118.0, 3, 1.0)], "close"), cons)
cons, _, _ = sc.walk(A, 0, "PE", 104.0, lambda j: 90.0)
check("a put: the stop above, touched in the third candle", cons == ([(104.0, 2, 1.0)], "stop"), cons)

print("3. CHARGES AT SIZE")
q = {"p0": 120.0, "p1": 130.0, "qty": 65, "exch": "NSE", "index": "NIFTY"}
one, fifty = sc.net(q, 1), sc.net(q, 50)
check("one lot: 10 points x 65 less slippage and charges", 500 < one < 650, round(one, 2))
check("50 lots of Nifty (3,250 qty) is two slices a side: four Rs 20 orders, against 100 for fifty one-lot trades",
      abs((fifty - 50 * one) - (50 * 2 - 4) * 20 * 1.18) < 0.01, round(fifty - 50 * one, 2))
check("half a point of slippage a side costs a point a contract", abs((sc.net(q, 50) - sc.net(q, 50, 0.5)) - 3250 * 1.0) < 25,
      round(sc.net(q, 50) - sc.net(q, 50, 0.5), 1))

print("4. MOMENTUM")
A3 = {"hi": np.array([100, 108, 112, 115.]), "lo": np.array([98, 100, 106, 111.]), "cl": np.array([99, 107, 111, 114.]),
      "end": np.array([False, False, False, True])}
P = {"open": np.array([99, 101, 107, 111.]), "adx": np.array([20, 27, 28, 29.]), "pdi": np.array([20, 30, 30, 30.]),
     "mdi": np.array([20, 15, 15, 15.]), "rsi": np.array([50, 65, 66, 67.])}
check("at the signal: the signal candle", sc.momentum("signal", 1, "CE", 95, A3, P) == (1, 107.0))
check("strong: a body of 6/8 = 75% the trade's way", sc.momentum("strong", 1, "CE", 95, A3, P) == (1, 107.0))
check("...not for a put", sc.momentum("strong", 1, "PE", 115, A3, P) is None)
check("ADX 27 rising, +DI over -DI", sc.momentum("adx", 1, "CE", 95, A3, P) == (1, 107.0))
check("RSI 65 for a call, not for a put", sc.momentum("rsi", 1, "CE", 95, A3, P) == (1, 107.0)
      and sc.momentum("rsi", 1, "PE", 115, A3, P) is None)
check("breakout: the next close above the signal candle's high", sc.momentum("breakout", 1, "CE", 95, A3, P) == (2, 111.0))
check("...none when the stop is touched first", sc.momentum("breakout", 1, "CE", 107.0, A3, P) is None)

print()
print("SCALP10 STUDY TEST PASSED" if not fails else f"SCALP10 STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
