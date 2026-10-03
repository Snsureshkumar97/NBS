#!/usr/bin/env python3
"""Hand-traced checks for exness_adx_study.price_cfd() - a CFD trade pays half
the quoted spread at entry and half at exit."""
import os, sys, tempfile
os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exness_adx_study as ex

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

check("a long from 84,000 to 84,500 with a 20 spread in and 30 out nets 500 - 10 - 15 = 475",
      ex.price_cfd("CE", 84000, 84500, 20, 30) == 475)
check("a short on the same move loses 500 plus the same 25 of spread",
      ex.price_cfd("PE", 84000, 84500, 20, 30) == -525)
check("a flat round trip costs exactly the average of the two spreads (here 25)",
      ex.price_cfd("CE", 2000.0, 2000.0, 0.2, 0.3) == -0.25)
check("units: XAUUSD is quoted per ounce and a lot is 100 oz", ex.SYMBOLS["XAUUSD"][1] == 100.0)
check("the sweep includes today's gate (20)", 20 in ex.ADX_VALUES)
print()
if fails:
    print(f"EXNESS ADX STUDY TEST FAILED - {len(fails)}"); sys.exit(1)
print("EXNESS ADX STUDY TEST PASSED")
