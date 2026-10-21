#!/usr/bin/env python3
"""Every vote must agree before a rules ticket, per index, from Trade settings (the user, 21 Oct 2026: "i want all 7
votes for the trade", after the live Nifty / Sensex calls bought on MACD, RSI and VWAP with Trend and PCR not voting
stopped out). Trend, MACD, RSI and VWAP all the trade's way, or the ticket is held; PCR the exception - it counts only
when it votes (the user, that evening); "3 of 5 (tested)" keeps the rule the studies measure. No network."""
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


def book(path=None):
    return tickets.TicketBook(path=path) if path else tickets.TicketBook()


def rec(side="CE", **votes):
    v = {"Trend": 1, "MACD": 1, "RSI": 1, "VWAP": 1, "PCR": 1}
    if side == "PE":
        v = {k: -1 for k in v}
    v.update(votes)
    return {"index": "NIFTY", "option_type": side, "votes": {k: x for k, x in v.items() if x is not None}}


print("1. THE DEFAULT AND THE CHOICES")
b = book()
check("config: 'all' by default, 'all' / 'tested' offered; the four required, PCR when it votes",
      config.VOTES_RULE_DEFAULT == "all" and tuple(config.VOTES_RULE_CHOICES) == ("all", "tested")
      and tuple(config.VOTES_ALL_NAMES) == ("Trend", "MACD", "RSI", "VWAP") and tuple(config.VOTES_WHEN_CAST) == ("PCR",))
check("an Indian index nobody has set: 'all'", b.votes_rule_for("NIFTY") == "all" and b.votes_rule_for("MIDCPNIFTY") == "all")
check("crypto has no such rule (its own votes)", tickets.TicketBook(market="crypto").votes_rule_for("BTC") is None)

print("2. THE HOLD")
b.systems_by.update(NIFTY="rules")
check("all five the trade's way: no hold", b._votes_hold("NIFTY", rec()) is None and b._votes_hold("NIFTY", rec("PE")) is None)
h = b._votes_hold("NIFTY", rec(Trend=0, PCR=0))
check("21 Oct's call: Trend not voting -> NOT ALL VOTES, Trend named, 3 of 4 (PCR neutral is not held against it)",
      h and h[0] == "all_votes" and h[1] == "NOT ALL VOTES" and "Trend is not voting" in h[2]
      and "PCR is not voting" not in h[2] and "3 of 4 agree for a call" in h[2], h)
print("   PCR, THE EXCEPTION (the user, 21 Oct 2026 evening: 'make as an exception just to show when it counts')")
check("the four agree, PCR neutral: no hold", b._votes_hold("NIFTY", rec(PCR=0)) is None)
check("the four agree, no option chain at all: no hold", b._votes_hold("NIFTY", rec(PCR=None)) is None)
h = b._votes_hold("NIFTY", rec(PCR=-1))
check("the four agree but PCR votes AGAINST: held, PCR named", h and "PCR is against" in h[2] and "4 of 4 agree" in h[2], h)
check("PCR for the trade: no hold", b._votes_hold("NIFTY", rec(PCR=1)) is None)
h = b._votes_hold("NIFTY", rec("PE", MACD=1))
check("a put with MACD against: held, MACD named as against", h and "MACD is against" in h[2] and "for a put" in h[2], h)
check("no direction (no option type): nothing to hold", b._votes_hold("NIFTY", {"votes": {}}) is None)

print("3. '3 OF 5 (TESTED)', AND THE OTHER SYSTEMS")
b.configure(votes="tested", votes_index="NIFTY")
check("set to tested: the same reading is not held here", b._votes_hold("NIFTY", rec(Trend=0, PCR=0)) is None)
b.configure(votes="all", votes_index="NIFTY")
b.systems_by["NIFTY"] = "trend_rider"
check("the Trend Rider keeps its own rule", b._votes_hold("NIFTY", rec(Trend=0, PCR=0)) is None)
b.systems_by["NIFTY"] = "tv_rating"
check("the TradingView rating keeps its own rule", b._votes_hold("NIFTY", rec(Trend=0, PCR=0)) is None)
b.systems_by["NIFTY"] = "rules"
b.configure(votes="sometimes", votes_index="NIFTY")
b.configure(votes="tested", votes_index="NOPE")
check("an unknown rule or index changes nothing", b.votes_rule_for("NIFTY") == "all" and "NOPE" not in b.votes_by)

print("4. KEPT, AND SENT TO THE PAGE")
d = tempfile.mkdtemp()
p = os.path.join(d, "trades.csv")
b1 = book(p)
b1.configure(votes="tested", votes_index="SENSEX")
b2 = book(p)
check("saved per index and read back after a restart", b2.votes_rule_for("SENSEX") == "tested"
      and b2.votes_rule_for("NIFTY") == "all")
ses = b2.session()
check("the session carries each index's rule and the choices", ses.get("votes_by", {}).get("SENSEX") == "tested"
      and ses.get("votes_by", {}).get("NIFTY") == "all" and ses.get("votes_choices") == ["all", "tested"], ses.get("votes_by"))
check("crypto's session: none", tickets.TicketBook(market="crypto").session().get("votes_by") == {})

print("5. WIRED INTO THE CHECKS AND THE PAGE")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickets.py")).read()
check("checked before a ticket and before a re-arm", src.count("self._votes_hold(book.name, rec)") == 2)
web = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("Trade settings: a Votes dropdown per index, saved through /api/ticket",
      'select data-votes="${esc(k)}"' in web and "body: new URLSearchParams({votes: want, votes_index: k})" in web
      and 'votes=(form.get("votes") or None), votes_index=(form.get("votes_index") or "").upper() or None' in web)
check("the Gates tab shows it on the Votes step", 'const allv = code === "all_votes";' in web)

print()
print("ALL VOTES RULE TEST PASSED" if not fails else f"ALL VOTES RULE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
