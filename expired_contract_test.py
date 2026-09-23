#!/usr/bin/env python3
"""A contract does not vanish from Zerodha's daily instrument dump the moment
it expires - NIFTY's Tuesday expiry was still listed, with zero open interest
and no quotes, on the Wednesday morning after (23 Sep 2026). Sorting the dump
for "the nearest expiry" without first dropping what is already in the past
picked that dead contract - so PCR read None (nothing on either side of a
quote that cannot be got), and every other reading off the chain (targets,
spread, the lot) was for an instrument that had already settled, not the one
actually trading. Fakes only - no Zerodha call is made.
"""
import datetime as dt
import os
import shutil
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import data_providers as dp

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


TODAY = dp._now_ist_naive().date()
YESTERDAY = TODAY - dt.timedelta(days=1)
NEXT_WEEK = TODAY + dt.timedelta(days=6)
FAR = TODAY + dt.timedelta(days=13)


def inst(name, itype, strike, expiry, tok, symbol):
    return {"tradingsymbol": symbol, "name": name, "instrument_type": itype, "strike": strike,
            "expiry": expiry, "lot_size": 65, "instrument_token": tok}


NFO = [
    # A dead contract Zerodha's dump has not dropped yet - the exact shape
    # that triggered this: same underlying, same strikes, expiry already in
    # the past, still sitting in the list.
    inst("NIFTY", "CE", 23400.0, YESTERDAY, 1001, "DEAD23400CE"),
    inst("NIFTY", "PE", 23400.0, YESTERDAY, 1002, "DEAD23400PE"),
    # The contract actually trading today.
    inst("NIFTY", "CE", 23400.0, NEXT_WEEK, 2001, "LIVE23400CE"),
    inst("NIFTY", "PE", 23400.0, NEXT_WEEK, 2002, "LIVE23400PE"),
    inst("NIFTY", "CE", 23500.0, NEXT_WEEK, 2003, "LIVE23500CE"),
    inst("NIFTY", "PE", 23500.0, NEXT_WEEK, 2004, "LIVE23500PE"),
    # A month out, so the sort is genuinely tested, not just two entries.
    inst("NIFTY", "CE", 23400.0, FAR, 3001, "FAR23400CE"),
    inst("NIFTY", "PE", 23400.0, FAR, 3002, "FAR23400PE"),
    # A different underlying on the same exchange must not leak in.
    inst("BANKNIFTY", "CE", 50000.0, NEXT_WEEK, 4001, "BN50000CE"),
]


class FakeKite:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else list(NFO)
        self.quote_calls = []
    def instruments(self, exchange):
        return list(self.rows) if exchange == "NFO" else []
    def quote(self, symbols):
        self.quote_calls.append(list(symbols))
        # The dead contract: no OI, no LTP, no depth - exactly what Zerodha's
        # quote endpoint gives back for a settled instrument, which is what
        # actually surfaced this (PCR needs a live call side to divide by).
        out = {}
        for s in symbols:
            if s.startswith("NFO:DEAD"):
                out[s] = {"oi": 0, "last_price": None, "depth": {}}
            else:
                out[s] = {"oi": 500, "last_price": 120.5,
                          "depth": {"buy": [{"price": 120.0}], "sell": [{"price": 121.0}]}}
        return out
    def ltp(self, symbols):
        return {symbols[0]: {"last_price": 23450.0}}


def provider(kite):
    p = dp.KiteDataProvider.__new__(dp.KiteDataProvider)
    p.kite = kite
    p._instrument_cache, p._option_inst_cache, p._equity_token_cache = {}, {}, None
    return p


def fresh():
    """A cold start - memory AND disk, so a fake instrument list from one
    section is never served to the next by the disk cache underneath."""
    dp._INSTR_ROWS.clear(); dp._INDEX_TOKENS.clear(); dp._INSTR_BLOCK.clear()
    shutil.rmtree(os.path.join(os.environ["TRADING_TOOL_HOME"], "cache"), ignore_errors=True)


print("1. THE DEAD EXPIRY NEVER SURVIVES THE FILTER")
fresh()
p = provider(FakeKite())
exch, opts = p._all_option_instruments("NIFTY")
expiries_seen = {i["expiry"] for i in opts}
check("yesterday's expiry is filtered out entirely, not just deprioritised",
      YESTERDAY not in expiries_seen, expiries_seen)
check("today's and the future ones are kept, all of them", {NEXT_WEEK, FAR} <= expiries_seen)
check("only NIFTY's own contracts - BankNifty's do not leak in through the same exchange",
      not any(t["strike"] == 50000.0 for t in opts), [t["strike"] for t in opts])

print("2. get_expiry_dates() NEVER OFFERS THE DEAD ONE EITHER")
dates = p.get_expiry_dates("NIFTY")
check("the list starts with the real nearest expiry, not the settled one",
      dates[0] == str(NEXT_WEEK), dates)
check("the dead one is nowhere in the list, not just moved to the back",
      str(YESTERDAY) not in dates, dates)

print("3. get_option_chain() WITH NO EXPIRY NAMED PICKS THE LIVE ONE")
fresh()
p2 = provider(FakeKite())
chain = p2.get_option_chain("NIFTY")
check("the nearest expiry it prices is the live one, not the dead one",
      chain["expiry"] == str(NEXT_WEEK), chain["expiry"])
check("PCR is a real number - the live contract actually has open interest on both sides",
      chain["pcr"] is not None and chain["pcr"] > 0, chain["pcr"])
check("every strike in the chain carries a real quote, not the dead contract's empty one",
      all(s["call_oi"] > 0 and s["call_ltp"] is not None for s in chain["strikes"]), chain["strikes"])
check("only the live symbols were ever quoted - the dead ones were never even asked for",
      not any("DEAD" in sym for call in p2.kite.quote_calls for sym in call), p2.kite.quote_calls)

print("4. THE DEAD EXPIRY CAN STILL BE ASKED FOR EXPLICITLY, IT JUST IS NOT THE DEFAULT")
fresh()
p3 = provider(FakeKite())
chain2 = p3.get_option_chain("NIFTY", expiry=str(NEXT_WEEK))
check("naming the live expiry explicitly still works, same as before this fix",
      chain2["expiry"] == str(NEXT_WEEK) and chain2["pcr"] is not None)

print("5. EXPIRY DAY ITSELF STILL TRADES - NOT CUT BY AN OFF-BY-ONE")
fresh()
today_rows = [inst("NIFTY", "CE", 23400.0, TODAY, 5001, "TODAY23400CE"),
             inst("NIFTY", "PE", 23400.0, TODAY, 5002, "TODAY23400PE")]
p4 = provider(FakeKite(rows=today_rows))
exch4, opts4 = p4._all_option_instruments("NIFTY")
check("an expiry dated today is kept, not treated as already past - it still trades until the close",
      {i["expiry"] for i in opts4} == {TODAY}, [i["expiry"] for i in opts4])

print("6. option_token() AND chain_tokens() SHARE THE SAME FILTERED LIST")
fresh()
p5 = provider(FakeKite())
tok = p5.option_token("NIFTY", 23400, "CE")
check("the nearest-expiry lookup (no expiry named) resolves to the live contract's token, not the dead one's",
      tok == 2001, tok)
tokens = p5.chain_tokens("NIFTY", str(YESTERDAY))
check("asking chain_tokens for the dead expiry by name finds nothing - it was filtered before this ever runs",
      tokens == {}, tokens)

print("EXPIRED CONTRACT TEST PASSED" if not fails else f"EXPIRED CONTRACT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
