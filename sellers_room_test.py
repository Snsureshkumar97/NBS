#!/usr/bin/env python3
"""The seller limit on room to run, per index from Trade settings, off by default (the user, 21 Oct 2026: "can that
data be used in room to run to know how far can market go"). When on, a rules ticket also needs MIN_REWARD_RISK_T3 of
room up to the heaviest option-seller level within 1.5% in its direction, or it is held. No network."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


L = 1e5


def rec(side="CE", room=155.0, risk=114.0, levels=None):
    """21 Oct's Nifty call at 09:21: room 155, stop 114; calls above at 23,100 (67.4 L, 28 pts) and 23,200 (79.9 L)."""
    lv = levels if levels is not None else [{"strike": 23100.0, "oi": 67.4 * L, "dist": 28.0, "chg": None},
                                            {"strike": 23200.0, "oi": 79.9 * L, "dist": 128.0, "chg": None}]
    se = {"call": {"levels": lv, "band_pct": None, "adding": False}, "put": None, "minutes": None}
    if side == "PE":
        se = {"call": None, "put": {"levels": lv, "band_pct": None, "adding": False}, "minutes": None}
    return {"index": "NIFTY", "option_type": side, "reach_points": room, "risk_points": risk, "sellers": se}


print("1. OFF BY DEFAULT")
b = tickets.TicketBook()
b.systems_by["NIFTY"] = "rules"
check("config: off by default, off / on offered", config.SELLERS_ROOM_DEFAULT == "off"
      and tuple(config.SELLERS_ROOM_CHOICES) == ("off", "on"))
check("an index nobody has set: off - nothing held", b.sellers_room_for("NIFTY") == "off" and b._sellers_room_hold("NIFTY", rec()) is None)
check("crypto has no such limit", tickets.TicketBook(market="crypto").sellers_room_for("BTC") is None)

print("2. ON")
b.configure(sroom="on", sroom_index="NIFTY")
h = b._sellers_room_hold("NIFTY", rec())
check("21 Oct's call: the heaviest (23,200, 128 pts) leaves 128 of 114 risk - 1.12 to 1, passes",
      h is None, h)
h = b._sellers_room_hold("NIFTY", rec(risk=140.0))
check("the same with a 140-point stop: 128 / 140 = 0.91, under 1:1 - SELLERS IN THE WAY, the level named",
      h and h[0] == "sellers_room" and h[1] == "SELLERS IN THE WAY" and "79.9 lakh calls above at 23,200" in h[2]
      and "0.91 to 1" in h[2], h)
h = b._sellers_room_hold("NIFTY", rec(room=90.0, risk=100.0))
check("the tool's own room shorter still: capped at its own 90 - held, 0.90 to 1", h and "leave 90 points" in h[2], h)
h = b._sellers_room_hold("NIFTY", rec("PE", risk=140.0))
check("a put: puts below are what counts", h and "puts below" in h[2], h)
check("no seller levels (no chain): no hold", b._sellers_room_hold("NIFTY", rec(levels=[])) is None
      and b._sellers_room_hold("NIFTY", {"option_type": "CE", "risk_points": 100, "reach_points": 50}) is None)
b.systems_by["NIFTY"] = "tv_rating"
check("the TradingView rating keeps its own targets: no hold", b._sellers_room_hold("NIFTY", rec(risk=140.0)) is None)
b.systems_by["NIFTY"] = "rules"

print("3. KEPT, AND SENT TO THE PAGE")
d = tempfile.mkdtemp()
p = os.path.join(d, "trades.csv")
b1 = tickets.TicketBook(path=p)
b1.configure(sroom="on", sroom_index="SENSEX")
b1.configure(sroom="maybe", sroom_index="NIFTY")
b2 = tickets.TicketBook(path=p)
check("saved per index, read back after a restart; an unknown value changes nothing",
      b2.sellers_room_for("SENSEX") == "on" and b2.sellers_room_for("NIFTY") == "off")
ses = b2.session()
check("the session carries each index's choice and the choices", ses.get("sroom_by", {}).get("SENSEX") == "on"
      and ses.get("sroom_choices") == ["off", "on"], ses.get("sroom_by"))

print("4. WIRED IN")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickets.py")).read()
check("checked before a ticket and before a re-arm", src.count("self._sellers_room_hold(book.name, rec)") == 2)
web = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("Trade settings: a Seller limit dropdown per index, saved through /api/ticket",
      'select data-sroom="${esc(k)}"' in web and "body: new URLSearchParams({sroom: want, sroom_index: k})" in web
      and 'sroom=(form.get("sroom") or None), sroom_index=(form.get("sroom_index") or "").upper() or None' in web)
check("the Gates tab shows it on the Room step", 'const sroomHeld = code === "sellers_room";' in web)

print()
print("SELLERS ROOM TEST PASSED" if not fails else f"SELLERS ROOM TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
