#!/usr/bin/env python3
"""A stop that closes in profit is not a stop-out.

Reported by the user on 23 Sep 2026: "when the ticket exits at T1 - when it
comes back to T1 the journal thinks it is a loss, but if it gets stop loss at T1
it is a profit". Since the trailing stop shipped (22 Sep) a ticket that reaches
T1 and falls back closes as "stop-loss hit (trailed to T1)" - with money made.
Every reader that decided by those words alone called it a loss: the Signal
page's "stopped out" count, the Record card, the Journal's review ("By how it
ended: Stop"), the Journal's own note, the AI desk's record and the daily
summaries. They now go by the money where it is known, and by the "trailed to"
in the status where it is not (a ticket tracked on the index has no rupee
figure).

Real ticket engine, real logs on a temporary disk; nothing here can reach an
account.
"""
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ai_desk as ad
import config
import journal
import review_page
import tickets
import trade_log
import web_server

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 9, 22, 11, 0, 0, tzinfo=IST)
tickets.now_ist = lambda: NOW
TODAY = "2026-09-22"

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. WHAT COUNTS AS A STOP-OUT")
tr = "CLOSED — stop-loss hit (trailed to T1)"
plain = "CLOSED — stop-loss hit"
live = "CLOSED — stop-loss hit (live order, Delta mark)"
check("a trailed stop is recognised as one", trade_log.is_trailed_close(tr) and not trade_log.is_trailed_close(plain))
check("a plain stop with a loss is a stop-out", trade_log.is_stop_out(plain, -1950.0))
check("a plain stop with no money figure is a stop-out", trade_log.is_stop_out(plain, None))
check("a trailed stop that made money is NOT a stop-out", not trade_log.is_stop_out(tr, 650.0))
check("a trailed stop on the index (no money figure) is NOT a stop-out", not trade_log.is_stop_out(tr, None))
check("a trailed stop that gapped through and lost IS a stop-out", trade_log.is_stop_out(tr, -325.0))
check("a trailed stop that came out exactly flat is not counted as a win", trade_log.is_stop_out(tr, 0.0))
check("a broker's stop that had trailed (its status does not say so) is told apart by its profit",
      not trade_log.is_stop_out(live, 300.0) and trade_log.is_stop_out(live, -300.0))
check("a target, a flip or a clear is never a stop-out",
      not any(trade_log.is_stop_out(s, 100.0) for s in
              ("CLOSED — T2 hit (full target reached)", "CLOSED — signal changed before target/SL was hit",
               "CLOSED — cleared manually")))
check("no status, no stop-out", not trade_log.is_stop_out(None, -5.0) and not trade_log.is_stop_out("", None))


print("2. REAL CLOSES, READ BACK OFF THE LOG")
EMAIL = "trailed-outcome@example.invalid"


def mk(tid, use_premium=True, entry=90.0, targets=(105.0, 114.0, 120.0), stop=70.0, exit_at="T3", index_entry=25000.0):
    return {"index": "NIFTY", "option_type": "CE", "strike": 25000,
            "entry_time": "10:00:00", "entry_spot": index_entry,
            "entry_ltp": entry if use_premium else None,
            "use_premium": use_premium, "lot_size": 65, "lots": 1, "exit_at": exit_at,
            "index_targets": [] if use_premium else list(targets),
            "index_sl": None if use_premium else stop,
            "premium_targets": list(targets) if use_premium else [],
            "premium_sl": stop if use_premium else None,
            "hit": {"T1": False, "T2": False, "T3": False},
            "hit_time": {"T1": None, "T2": None, "T3": None},
            "sl_hit": False, "sl_hit_time": None, "status": "OPEN", "trade_id": tid}


book = tickets.TicketBook(owner=EMAIL, market="nse_index")
LOG = book.path


def run(tid, prices, **kw):
    """One ticket driven through these prices; returns its final status."""
    book.books["NIFTY"].trade = mk(tid, **kw)
    for p in prices:
        book.tick_price("NIFTY", p)
    t = book.books["NIFTY"].trade
    return t["status"] if t else None


s1 = run("win-trailed", [108.0, 100.0])                       # T1 crossed, back to T1: +650
s2 = run("loss-plain", [60.0])                                # straight to the stop: -1950
s3 = run("loss-gap", [108.0, 85.0])                           # trailed to T1, then gapped under the entry: -325
s4 = run("win-index", [25070.0, 25055.0], use_premium=False, targets=(25060.0, 25110.0, 25160.0),
         stop=24940.0)                                       # tracked on the index: no rupee figure
book.books["NIFTY"].trade = mk("win-live"); book.books["NIFTY"].trade["hit"]["T1"] = True
book.close_ticket("NIFTY", live, price=100.0)                # a broker stop that had trailed: +650, status silent about it
book.books["NIFTY"].trade = mk("t2-target")
book.tick_price("NIFTY", 121.0)                              # straight through the exit rung: T3 hit
check("the closes are what was meant to be tested",
      "trailed to T1" in s1 and s2 == "CLOSED — stop-loss hit" and "trailed to T1" in s3 and "trailed to T1" in s4,
      (s1, s2, s3, s4))
rows = [r for r in trade_log._read_rows(LOG) if r.get("event") == "CLOSE"]
check("all six were logged", len(rows) == 6, len(rows))
pn = {r["trade_id"]: r.get("pnl") for r in rows}
check("the trailed win's money is a profit and the plain stop's a loss",
      float(pn["win-trailed"]) == 650.0 and float(pn["loss-plain"]) == -1950.0 and float(pn["loss-gap"]) == -325.0, pn)
check("the index-tracked one has no rupee figure", pn["win-index"] in ("", None), pn["win-index"])

issued, wins, stops, locked = trade_log.day_outcomes(TODAY, path=LOG)
check("the day's tally: one ran to target, two lost to their stop, THREE closed in profit on a trailed or broker stop",
      (wins, stops, locked) == (1, 2, 3), (wins, stops, locked))
check("day_counts still answers in three numbers, with stops meaning only the losses",
      trade_log.day_counts(TODAY, path=LOG) == (issued, 1, 2))
b2 = tickets.TicketBook(owner=EMAIL, market="nse_index")
check("the ticket book reads the same off its own log", b2.day_stats() == (issued, 1, 2, 3), b2.day_stats())
sess = b2.session()
check("the session the page gets carries all four, with 'stops' the losses only",
      (sess["wins"], sess["stops"], sess["locked"]) == (1, 2, 3), (sess["wins"], sess["stops"], sess["locked"]))
b_empty = tickets.TicketBook(owner="empty-trailed@example.invalid", market="nse_index")
check("a book with no log at all reads zeros, not an error", b_empty.day_stats() == (0, 0, 0, 0), b_empty.day_stats())


print("3. THE RECORD CARD")
rec = web_server.track_record(EMAIL, "nse_index")
check("'Stopped out' is the trades that lost to their stop: 2 of 6, not 5 of 6",
      rec["sl"] == round(100 * 2 / 6, 1), rec["sl"])
check("and the trailed exits have a figure of their own: 3 of 6", rec["trailed"] == 50.0, rec["trailed"])
check("the wins and losses beside it are unchanged (money decides; the index-tracked one has none)",
      rec["wins"] == 3 and rec["losses"] == 2, (rec["wins"], rec["losses"]))


print("4. THE JOURNAL")
lines = journal.entries(EMAIL, "nse_index", "tool")
by = {e["id"]: e for e in lines}
check("the trailed win's note says it made money, in words",
      by["win-trailed"]["status"] == "trailed stop hit - closed in profit (the stop had moved up to T1)", by["win-trailed"]["status"])
check("...and so does a broker stop that had trailed", by["win-live"]["status"] == "trailed stop hit - closed in profit", by["win-live"]["status"])
check("a plain stop-out keeps its own words", by["loss-plain"]["status"] == plain, by["loss-plain"]["status"])
check("a trailed stop that gapped under the entry keeps its own words - it lost",
      by["loss-gap"]["status"] == tr, by["loss-gap"]["status"])
summ = journal.summarize(lines)["stats"]
check("the journal's own win/loss was always the money: 3 won, 2 lost (the index-tracked ticket has no figure)",
      summ["wins"] == 3 and summ["losses"] == 2, (summ["wins"], summ["losses"]))
rv = review_page.build(EMAIL, "nse_index")
ended = dict(rv["groups"]["By how it ended"])
check("the Journal's review files the winners under their own heading, not under 'Stop'",
      set(ended) >= {"Stop", "Trailed stop (profit)", "Target"}, sorted(ended))
check("'Stop' holds only the two that lost", ended["Stop"]["n"] == 2, ended["Stop"]["n"])
check("'Trailed stop (profit)' holds the winners - the two rupee ones, and the broker stop", ended["Trailed stop (profit)"]["n"] == 2,
      ended["Trailed stop (profit)"]["n"])
check("...and every one of them made money", ended["Trailed stop (profit)"]["win"] == 100.0, ended["Trailed stop (profit)"]["win"])
ek = review_page._exit_kind
check("review_page names each ending correctly",
      ek(tr, 650.0) == "Trailed stop (profit)" and ek(plain, -1950.0) == "Stop" and ek(tr, -325.0) == "Stop"
      and ek(tr, None) == "Trailed stop (profit)" and ek(plain, None) == "Stop" and ek(live, 300.0) == "Trailed stop (profit)")


print("5. THE AI DESK'S RECORD, WHICH THE BOT LEARNS FROM")
ak = ad.AIDesk._exit_kind
check("an AI trade the trailed stop closed in profit is 'trailed_stop', not 'stop'",
      ak(tr, 650.0) == "trailed_stop" and ak(live, 300.0) == "trailed_stop" and ak(tr, None) == "trailed_stop")
check("one that lost to its stop is still 'stop'", ak(plain, -1950.0) == "stop" and ak(tr, -325.0) == "stop" and ak(plain, None) == "stop")
check("targets, exits and the rest are as before",
      ak("CLOSED — T3 hit (full target reached)", 500.0) == "target" and ak("CLOSED — AI exit: x", 10.0) == "your_exit"
      and ak("CLOSED — give-back rule x", 10.0) == "give_back_rule")


class FakeFeed:
    def __init__(self, email):
        self.market, self.email, self.key = "nse_index", email, f"{email}#nse_index"
        self.lock = threading.RLock()
        self.tickets = tickets.TicketBook(owner=email, market="nse_index")
        self.streamer, self.dstream, self.faults = None, None, []
        self.state = {"feed": "ok", "indices": {}}
    def instruments(self):
        return config.instruments_in("nse_index")
    def snapshot(self):
        return {"indices": {}, "tickets": {}, "session": {}}
    def _note_fault(self, where, text):
        self.faults.append((where, text))


f = FakeFeed("trailed-ai@example.invalid")
d = ad.AIDesk(f, now=lambda: NOW, clock=lambda: 1_789_000_000.0, start=False)
d.book.books["NIFTY"].trade = mk("ai-win")
d.book.tick_price("NIFTY", 108.0)
d.book.tick_price("NIFTY", 100.0)
d.book.books["NIFTY"].trade = mk("ai-loss")
d.book.tick_price("NIFTY", 60.0)
d.book.books["NIFTY"].trade = mk("ai-gap")
d.book.tick_price("NIFTY", 108.0)
d.book.tick_price("NIFTY", 85.0)                  # trailed to T1, then gapped under the entry: -325
tr_rec = d.track_record("NIFTY")
be = tr_rec["by_exit"]
check("the bot's 'by_exit' keeps the winner out of 'stop': one trailed_stop that won, two stops that lost "
      "(the plain one and the one that gapped under the entry)",
      be.get("trailed_stop", {}).get("n") == 1 and be.get("stop", {}).get("n") == 2
      and be["trailed_stop"]["win_rate_pct"] == 100 and be["stop"]["win_rate_pct"] == 0, be)
how = {t["pnl"]: t["how_it_ended"] for t in tr_rec["your_last_trades_on_this_index"]}
check("and its latest trades say how each really ended, by the money",
      how == {650.0: "trailed_stop", -1950.0: "stop", -325.0: "stop"}, how)


print("6. THE DAILY SUMMARY AND THE OLD REVIEW TOOLS")
text = trade_log.build_summary(TODAY, path=LOG)
check("the daily summary counts losses to the stop apart from trailed profits",
      "Stopped out at a loss: 2/6" in text and "Trailed out in profit: 3/6" in text)
import review
check("review.hit('sl_hit') counts only the trades that lost to their stop",
      review.hit(rows, "sl_hit") == 2, review.hit(rows, "sl_hit"))
check("...and the target fields are untouched", review.hit(rows, "t1_hit") == sum(1 for r in rows if str(r["t1_hit"]).lower() == "true"))
import contextlib
import io
import shutil as _sh
import churn_report
_sh.copy(LOG, trade_log._log_path())                 # churn_report reads the default log
buf = io.StringIO()
argv, sys.argv = sys.argv, ["churn_report.py", TODAY]
try:
    with contextlib.redirect_stdout(buf):
        churn_report.main()
finally:
    sys.argv = argv
rep = buf.getvalue()
check("churn_report lists the trailed profits as their own ending, apart from 'stopped out'",
      "trailed stop (closed in profit)" in rep and "stopped out" in rep)
tline = next((l for l in rep.splitlines() if "trailed stop (closed in profit)" in l), "")
sline = next((l for l in rep.splitlines() if l.strip().startswith("stopped out")), "")
check("...three of them, and two that lost", " 3 " in tline + " " and " 2 " in sline + " ", (tline, sline))
import analyse_log
check("analyse_log files a trailed profit apart from a stop-out",
      analyse_log.bucket(tr, 650.0) == "trailed stop (closed in profit)" and analyse_log.bucket(plain, -5.0) == "stopped out"
      and analyse_log.bucket(tr, None) == "trailed stop (closed in profit)")


import market_bot
check("the bot is handed the trailed-out count with the other session tallies", "locked" in market_bot.SESSION_FIELDS)
check("...and both of its prompts say what the new words mean",
      "trailed_stop" in market_bot.DESK_SYSTEM and "locked" in market_bot.SYSTEM)

print("7. THE PAGE")
src = open(os.path.join(HERE, "web_server.py")).read()
check("the Signal page's session bar says how many were trailed out in profit, apart from those stopped out",
      "<b>${sess.locked}</b> trailed out in profit" in src and "<b>${sess.stops}</b> stopped out" in src)
check("the Record card has a tile for them, and 'Stopped out' says it is at a loss",
      'tile("Trailed out"' in src and 'tile("Stopped out", rec.sl+"%","at a loss")' in src)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's recap", False, "install node to run this section")
else:
    a = src.index("function recapDraw(s){")
    b = src.index("\n}\n", a) + 3
    prog = """
const assert = require("assert");
const EL = {};
const $ = id => EL[id] || (EL[id] = {innerHTML: "", textContent: ""});
const esc = s => String(s), money = v => "R" + v, num = v => String(v);
let CUR = "NIFTY";
""" + src[a:b] + r'''
recapDraw({session: {issued: 6, wins: 1, stops: 2, locked: 3, booked: 100, open: 0, net: 100}, indices: {NIFTY: {trend: {}}}});
let h = $("recap").innerHTML;
assert.ok(h.includes("Trailed out in profit") && h.includes(">3<"), "the recap shows the trailed-out count - " + h);
assert.ok(h.indexOf("Trailed out in profit") < h.indexOf("Stopped out"), "...beside, not inside, the stopped-out count");
const up = h.slice(h.indexOf("Trailed out in profit"), h.indexOf("Stopped out"));
assert.ok(up.includes("var(--up)"), "and in the winning colour, where 'Stopped out' is the losing one - " + up);
recapDraw({session: {issued: 1, wins: 0, stops: 1, locked: 0, booked: -5, open: 0, net: -5}, indices: {NIFTY: {trend: {}}}});
assert.ok(!$("recap").innerHTML.includes("Trailed out in profit"), "no trailed exits: no empty cell for them");
console.log("ok:recap");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    check("the recap box shows trailed exits as their own winning number", "ok:recap" in r.stdout and r.returncode == 0, out[-800:])

print("TRAILED OUTCOME TEST PASSED" if not fails else f"TRAILED OUTCOME TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
