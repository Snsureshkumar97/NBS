#!/usr/bin/env python3
"""Zerodha's instrument list: downloaded once a day per exchange however many
providers are built, resolved from the cheap quote endpoint for an index, and
not hammered while Zerodha is refusing it. Written after the Indian market went
blank at the open on 21 Sep 2026 - a provider is rebuilt on every failed cycle,
each one re-downloaded the multi-megabyte list, and Zerodha answered "Too many
requests" to all of them, so no index token could be resolved and the failure
fed itself. Fakes only; no Zerodha call is made."""
import os
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


ROWS = {"NSE": [{"tradingsymbol": "NIFTY 50", "segment": "INDICES", "instrument_token": 256265},
                {"tradingsymbol": "RELIANCE", "segment": "NSE", "instrument_type": "EQ", "instrument_token": 738561}],
        "BSE": [{"tradingsymbol": "SENSEX", "segment": "BSE-INDICES", "instrument_token": 265}]}


class FakeKite:
    """Counts what a real Zerodha would be asked for."""
    def __init__(self, dump_fails=None, ltp_fails=False):
        self.dumps, self.ltps, self.dump_fails, self.ltp_fails = [], [], dump_fails, ltp_fails
    def instruments(self, exchange):
        self.dumps.append(exchange)
        if self.dump_fails:
            raise self.dump_fails
        return list(ROWS.get(exchange, []))
    def ltp(self, syms):
        self.ltps.append(list(syms))
        if self.ltp_fails:
            raise RuntimeError("no quote")
        s = syms[0]
        return {s: {"instrument_token": {"NSE:NIFTY 50": 256265, "BSE:SENSEX": 265}[s], "last_price": 23350.0}}


def fresh():
    dp._INSTR_ROWS.clear(); dp._INDEX_TOKENS.clear(); dp._INSTR_BLOCK.clear()


def provider(kite):
    p = dp.KiteDataProvider.__new__(dp.KiteDataProvider)     # no network in __init__
    p.kite = kite
    p._instrument_cache, p._option_inst_cache, p._equity_token_cache = {}, {}, None
    return p


print("1. AN INDEX TOKEN COMES FROM THE QUOTE ENDPOINT, NOT THE MULTI-MEGABYTE LIST")
fresh()
k = FakeKite()
check("the token is read from the quote, and the instrument list is never downloaded for it",
      provider(k)._instrument_token("NIFTY") == 256265 and k.ltps == [["NSE:NIFTY 50"]] and k.dumps == [])
check("a second, freshly built provider asks Zerodha nothing at all - the token is the day's, not the object's",
      provider(k)._instrument_token("NIFTY") == 256265 and len(k.ltps) == 1 and k.dumps == [])
fresh()
k2 = FakeKite(ltp_fails=True)
check("if the quote fails, the list is the fallback and still answers",
      provider(k2)._instrument_token("NIFTY") == 256265 and k2.dumps == ["NSE"])
check("...and that fallback is also downloaded once, however many providers ask",
      provider(k2)._instrument_token("NIFTY") == 256265 and k2.dumps == ["NSE"])

print("2. ONE DOWNLOAD PER EXCHANGE PER DAY, SHARED BY EVERY READER")
fresh()
k3 = FakeKite()
rows = [dp._instruments_cached(k3, "NSE") for _ in range(4)]
check("four readers, one download", k3.dumps == ["NSE"] and all(r == ROWS["NSE"] for r in rows))
dp._instruments_cached(k3, "BSE")
check("a different exchange is its own download", k3.dumps == ["NSE", "BSE"])
day = dp._now_ist_naive().date()
fresh()
dp._INSTR_ROWS[("NSE", day.replace(year=day.year - 1))] = ["stale"]
check("a copy from another day is never served", dp._instruments_cached(k3, "NSE") == ROWS["NSE"]
      and k3.dumps == ["NSE", "BSE", "NSE"])
check("...and it is dropped rather than held in memory all day",
      not [key for key in dp._INSTR_ROWS if key[1] != day])
check("the futures rows, the equity tokens and the option list all read the shared copy",
      "_instruments_cached(self.kite, exch)" in open(os.path.join(HERE, "data_providers.py")).read()
      and "self.kite.instruments(" not in open(os.path.join(HERE, "data_providers.py")).read())

print("3. A REFUSAL IS REMEMBERED, NOT RETRIED AT ONCE")
fresh()
boom = Exception("Too many requests")
k4 = FakeKite(dump_fails=boom)
for i in range(3):
    try:
        dp._instruments_cached(k4, "NSE")
    except Exception as exc:
        last = exc
check("Zerodha is asked once, then told back to the caller without another call", len(k4.dumps) == 1)
check("the message says what happened and when it will be tried again",
      "refusing its instrument list" in str(last) and "NSE" in str(last), str(last))
dp._INSTR_BLOCK["NSE"] = 0                                   # the wait has passed
k4.dump_fails = None
check("once the wait is over it tries again and succeeds", dp._instruments_cached(k4, "NSE") == ROWS["NSE"]
      and len(k4.dumps) == 2)
fresh()
k5 = FakeKite(dump_fails=Exception("Insufficient permission for that call"))
for _ in range(2):
    try:
        dp._instruments_cached(k5, "NSE")
    except Exception:
        pass
check("any other failure is not treated as a rate limit - it is asked again", len(k5.dumps) == 2
      and not dp._INSTR_BLOCK)

print("INSTRUMENTS CACHE TEST PASSED" if not fails else f"INSTRUMENTS CACHE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
