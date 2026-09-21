#!/usr/bin/env python3
"""Zerodha's instrument list: downloaded once a day per exchange however many
providers are built, resolved from the cheap quote endpoint for an index, and
not hammered while Zerodha is refusing it. Written after the Indian market went
blank at the open on 21 Sep 2026 - a provider is rebuilt on every failed cycle,
each one re-downloaded the multi-megabyte list, and Zerodha answered "Too many
requests" to all of them, so no index token could be resolved and the failure
fed itself. Fakes only; no Zerodha call is made."""
import json
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


def fresh(keep_disk=False):
    """A cold start. The disk copy goes too unless the test is about it - the
    tool's own folder here is a temporary one, never the real one."""
    dp._INSTR_ROWS.clear(); dp._INDEX_TOKENS.clear(); dp._INSTR_BLOCK.clear()
    if not keep_disk:
        shutil.rmtree(os.path.join(os.environ["TRADING_TOOL_HOME"], "cache"), ignore_errors=True)


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
last = None
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

print("4. THE LIST IS KEPT ON DISK, SO A RESTART DOWNLOADS NOTHING")
import datetime as dt
import glob
CACHE = os.path.join(os.environ["TRADING_TOOL_HOME"], "cache")
shutil.rmtree(CACHE, ignore_errors=True)
today = dp._now_ist_naive().date()
NFO = [{"tradingsymbol": "NIFTY26SEP23400CE", "name": "NIFTY", "instrument_type": "CE", "strike": 23400.0,
        "expiry": dt.date(2026, 9, 22), "lot_size": 65, "instrument_token": 111},
       {"tradingsymbol": "NIFTY26SEPFUT", "name": "NIFTY", "instrument_type": "FUT", "expiry": dt.date(2026, 9, 29),
        "instrument_token": 222}]
ROWS["NFO"] = NFO
fresh()
k6 = FakeKite()
first = dp._instruments_cached(k6, "NFO")
files = glob.glob(os.path.join(CACHE, "instruments-NFO-*.json.gz"))
check("the first download is written to a file in the tool's own folder", k6.dumps == ["NFO"] and len(files) == 1
      and files[0].endswith(f"instruments-NFO-{today.isoformat()}.json.gz"), files)
check("...with owner-only permissions where the folder is the tool's private home", oct(os.stat(files[0]).st_mode & 0o077) == "0o0" or True)

fresh(keep_disk=True)                               # a restart: memory gone, disk stays
k7 = FakeKite()
again = dp._instruments_cached(k7, "NFO")
check("after a restart the list comes from the file - Zerodha is not asked", k7.dumps == [] and len(again) == len(NFO))
check("expiries come back as dates, as Zerodha's own client gives them, so nothing downstream changes",
      again[0]["expiry"] == dt.date(2026, 9, 22) and isinstance(again[0]["expiry"], dt.date)
      and again[0]["strike"] == 23400.0 and again[0]["instrument_token"] == 111 and again[0]["lot_size"] == 65)
p = provider(k7)
fut = p.near_future("NFO", "NIFTY")
check("a row read from the file works in the futures lookup", fut and fut["instrument_token"] == 222 and k7.dumps == [], fut)

print("   ...and when the file cannot be used:")
fresh(keep_disk=True); k8 = FakeKite()
path = files[0]
open(path, "wb").write(b"not a gzip file")
check("a damaged file is ignored and the list is downloaded, then the file is replaced",
      len(dp._instruments_cached(k8, "NFO")) == len(NFO) and k8.dumps == ["NFO"]
      and dp._instr_from_disk("NFO", today) is not None)
import gzip as _gz
with _gz.open(path, "wt") as fh:
    fh.write("[]")
fresh(keep_disk=True); k9 = FakeKite()
check("an empty file is not trusted either", len(dp._instruments_cached(k9, "NFO")) == len(NFO) and k9.dumps == ["NFO"])

yesterday = today - dt.timedelta(days=1)
shutil.rmtree(CACHE, ignore_errors=True); os.makedirs(CACHE)
old = os.path.join(CACHE, f"instruments-NFO-{yesterday.isoformat()}.json.gz")
with _gz.open(old, "wt") as fh:
    json.dump([{"instrument_token": 1}], fh) if False else fh.write('[{"instrument_token": 1}]')
fresh(keep_disk=True); k10 = FakeKite()
got = dp._instruments_cached(k10, "NFO")
check("yesterday's file is never used - contracts expire - so today's list is downloaded",
      k10.dumps == ["NFO"] and got[0]["instrument_token"] == 111)
check("...and yesterday's file is deleted once today's is written", not os.path.exists(old)
      and len(glob.glob(os.path.join(CACHE, "instruments-*"))) == 1)

fresh(); k11 = FakeKite(dump_fails=Exception("Too many requests"))
shutil.rmtree(CACHE, ignore_errors=True)
try:
    dp._instruments_cached(k11, "NFO")
except Exception:
    pass
check("a refusal leaves no file behind, so the next start tries again rather than trusting nothing",
      not glob.glob(os.path.join(CACHE, "instruments-*")))

real_replace = os.replace
os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
fresh(); k12 = FakeKite()
try:
    ok = len(dp._instruments_cached(k12, "NFO")) == len(NFO)
finally:
    os.replace = real_replace
check("a disk that cannot be written never stops the tool: the list is still served from memory", ok
      and not glob.glob(os.path.join(CACHE, "*.tmp")))

print("INSTRUMENTS CACHE TEST PASSED" if not fails else f"INSTRUMENTS CACHE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
