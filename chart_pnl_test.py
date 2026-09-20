#!/usr/bin/env python3
"""The open ticket on the charts: its own levels, its entry, and its P&L.

17 Sep 2026, asked for by the user: "does it show P&L on the chart from the
entry of our signal". It did not, and the index chart drew the LIVE signal's
levels even while a ticket was open - which can drift away from the frozen ones.

Source checks, plus the ticket payload the page reads. The drawing was checked in
a browser on an isolated copy of the server (its own temporary home, no broker)
with synthetic candles: +Rs1,088 (+12.1%) on a premium ticket, +20 pts (+0.07%)
on one tracked on the index, no badge without a ticket, -Rs1,770 (-19.7%) in the
contract popup.
"""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()

print("1. THE TICKET CARRIES WHAT THE CHART NEEDS")
trade = {"index": "NIFTY", "strike": 23200, "expiry": "2026-09-22", "option_type": "CE",
         "status": "OPEN", "entry_time": "09:31:02", "entry_ltp": 120.0, "entry_spot": 23180.0,
         "use_premium": True, "lot_size": 75, "lots": 1,
         "premium_targets": [150.0, 170.0, 190.0], "premium_sl": 85.0,
         "index_targets": [23230.0, 23260.0, 23290.0], "index_sl": 23135.0,
         "hit": {"T1": False, "T2": False, "T3": False}, "hit_time": {"T1": None, "T2": None, "T3": None},
         "sl_hit": False, "sl_hit_time": None}
pub = tickets.TicketBook()._public_trade(trade, None, 134.5)
check("the frozen index levels", pub["index_targets"] == [23230.0, 23260.0, 23290.0] and pub["index_stop"] == 23135.0)
check("where the index was at entry, even for a premium ticket", pub["entry_spot"] == 23180.0, pub.get("entry_spot"))
check("the live P&L the signal card shows", pub["pnl"] == round((134.5 - 120.0) * 75, 2), pub["pnl"])

print("2. THE INDEX CHART")
check("reads the open ticket from the live state, like the signal card",
      "function openTicketFor(key){" in SRC and "const TK = openTicketFor(CH.key);" in SRC)
check("draws the ticket's frozen levels while it is open, the live signal's otherwise",
      "stop: TK.index_stop, entry: TK.entry_spot}" in SRC and ": ((d.levels)||{});" in SRC)
check("an Entry line among the levels", '[L.entry, "Entry", C.warn]' in SRC)
check("the entry is inside the price range the chart scales to", "for(const v of [L.t1,L.t2,L.t3,L.stop,L.entry]){" in SRC)
check("the P&L badge only while a ticket is open", "if(TK) pnlBadge(cx, PAD.l + 8, PAD.t + 6, TK);" in SRC)

print("3. THE P&L ITSELF")
body = SRC[SRC.index("function ticketPnl(t){"):SRC.index("function pnlBadge(")]
check("a premium ticket: money and % of the premium paid",
      "money(t.pnl)" in body and "(t.now - t.entry) / t.entry * 100" in body)
check("a ticket tracked on the index: points, signed for a put",
      't.option_type === "PE" ? e - spot : spot - e' in body and "pts" in body)
check("the sign is in the text, not only the colour", '"+" : "\u2212"' in body and body.count('"+" : "\u2212"') == 3)

print("4. THE CONTRACT POPUP")
check("the badge when the popup IS the open ticket's contract - the same P&L the signal card computes",
      "String(tk.strike) === String(OC.strike) && tk.option_type === OC.side" in SRC.split("function ocPnl()")[1][:1200]
      and "ticketPnl(tk)" in SRC.split("function ocPnl()")[1][:1200])
check("it keeps pace with the signal card while open",
      "setInterval(() => { if(OC.open && !document.hidden) ocPnl(); }, 1500);" in SRC)

print("CHART PNL TEST PASSED" if not fails else f"CHART PNL TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
