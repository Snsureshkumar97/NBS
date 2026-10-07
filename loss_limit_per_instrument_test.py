#!/usr/bin/env python3
"""The daily loss limit per instrument on the crypto market (config.DAILY_LOSS_LIMIT_PER_INSTRUMENT): one BTC
stop at 0.25 lot (~$175) was past the whole $120 limit and held gold's SELL for the rest of the day (the user,
5 Oct 2026: "remove btc loss and let gold take the trade"). BTC's loss now stops BTC only. The Indian indices
have no daily loss limit since 7 Oct 2026 (config.DAILY_LOSS_LIMIT_OFF). The real TicketBook.entry_block, with today's booked P&L stubbed."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import tickets
import trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
tickets.now_ist = lambda: dt.datetime(2026, 10, 5, 11, 0, tzinfo=IST)       # a Monday, mid-session
booked = {}
trade_log.booked_today = lambda date_str=None, path=None: (dict(booked), len(booked))

print("1. CRYPTO: EACH INSTRUMENT ANSWERS TO ITS OWN LOSSES")
cb = tickets.TicketBook(market="crypto", path=os.path.join(tempfile.mkdtemp(), "trades.csv"))
cb.capital, cb.risk_pct, cb.limits = 2000.0, 2.0, False
check("the limit is 3 x 2% of $2,000 = $120", cb.loss_limit() == 120.0, cb.loss_limit())
booked.update({"BTC": -143.61})                      # 5 Oct: +31.99 then -175.60
cb._booked_cache = None
b = cb.entry_block("BTC")
check("BTC, down $143.61 today, is held - LOSS LIMIT, naming BTC", b is not None and b[0] == "loss_limit" and "BTC" in b[2], b)
check("GOLD, with no loss of its own, is NOT held by BTC's loss", cb.entry_block("GOLD") is None, cb.entry_block("GOLD"))
booked.update({"GOLD": -130.0})
cb._booked_cache = None
b = cb.entry_block("GOLD")
check("...until gold's OWN closed trades pass the limit", b is not None and b[0] == "loss_limit" and "GOLD" in b[2], b)
check("the book's total is still known (for the page)", cb.booked_net_today() == round(-143.61 - 130.0, 2), cb.booked_net_today())

print("2. THE INDIAN INDICES: NO DAILY LOSS LIMIT (the user, 7 Oct 2026: 'does indian market have loss limit remove it')")
nb = tickets.TicketBook(market="nse_index", path=os.path.join(tempfile.mkdtemp(), "trades.csv"))
nb.capital, nb.risk_pct, nb.limits = 200000.0, 2.0, False         # the user's stored figures
check("DAILY_LOSS_LIMIT_OFF lists the Indian indices only", tuple(config.DAILY_LOSS_LIMIT_OFF) == ("nse_index",))
check("no limit even with a capital figure stored", nb.loss_limit() is None and nb.loss_limit_pct() == 0.0,
      (nb.loss_limit(), nb.loss_limit_pct()))
booked.clear(); booked.update({"BANKNIFTY": -11070.0, "SENSEX": -7860.0})     # 7 Oct by 10:18: -18,930
nb._booked_cache = None
check("7 Oct's -18,930 by 10:18 no longer holds NIFTY", nb.entry_block("NIFTY") is None, nb.entry_block("NIFTY"))
booked.update({"NIFTY": -50000.0})
nb._booked_cache = None
check("...nor a much worse day", nb.entry_block("SENSEX") is None, nb.entry_block("SENSEX"))
check("crypto is NOT in the off list - its per-instrument limit stays", cb.loss_limit() == 120.0)
saved = config.DAILY_LOSS_LIMIT_OFF
config.DAILY_LOSS_LIMIT_OFF = ()                     # the rollback: one limit across the three, as before
try:
    booked.clear(); booked.update({"BANKNIFTY": -7000.0, "SENSEX": -6000.0})
    nb._booked_cache = None
    check("rollback (list emptied): the limit is 6% of 2,00,000 = 12,000 again", nb.loss_limit() == 12000.0, nb.loss_limit())
    b = nb.entry_block("NIFTY")
    check("...and NIFTY is held by Bank Nifty's and Sensex's losses together (13,000 > 12,000)",
          b is not None and b[0] == "loss_limit" and "NIFTY " not in b[2].split("down")[0], b)
finally:
    config.DAILY_LOSS_LIMIT_OFF = saved
check("DAILY_LOSS_LIMIT_PER_INSTRUMENT lists crypto only", tuple(config.DAILY_LOSS_LIMIT_PER_INSTRUMENT) == ("crypto",))

print("3. EVERY ENTRY PATH ASKS WITH ITS OWN INSTRUMENT")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickets.py")).read()
check("no entry_block() call without the instrument", "self.entry_block()" not in src
      and src.count("self.entry_block(book.name)") == 3)

print()
print("LOSS LIMIT PER INSTRUMENT TEST PASSED" if not fails else f"LOSS LIMIT PER INSTRUMENT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
