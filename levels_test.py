#!/usr/bin/env python3
"""Fibonacci retracements for the Levels tab, checked by hand."""
import indicators as ind

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

f = ind.fib_retracements(23500.0, 23300.0)
check("a 200-point range: 23,376.40 / 23,400.00 / 23,423.60", f == {"382": 23376.4, "50": 23400.0, "618": 23423.6}, f)
up = 23300 + 200 * 0.382
down = 23500 - 200 * 0.618
check("38.2% up from the low is 61.8% down from the high", abs(up - down) < 1e-9 and f["382"] == round(up, 2))
check("ordered low to high inside the range", 23300 < f["382"] < f["50"] < f["618"] < 23500)
z = ind.fib_retracements(100, 100)
check("a flat session puts all three on the price", set(z.values()) == {100.0}, z)
print("LEVELS TEST PASSED" if not fails else f"LEVELS TEST FAILED: {fails}")
