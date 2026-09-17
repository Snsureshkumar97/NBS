"""The trading journal: entry checks, money, merging, statistics. Temp home only."""
import datetime as dt, os, sys, tempfile
os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import journal, trade_log, regime_study as rs

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)
EM, MK = "journal-test@example.invalid", "nse_index"
today = journal.today_ist()
def form(**kw):
    base = {"date": (today - dt.timedelta(days=3)).isoformat(), "time": "10:45", "instrument": "NIFTY",
            "side": "CE", "dir": "buy", "strike": "23400", "lots": "2", "lot_size": "", "entry": "120", "exit": "150",
            "charges": "", "notes": "breakout above the opening range"}
    base.update(kw); return base

print("1. WHAT A TRADE MUST BE")
def rejects(label, **kw):
    try:
        journal.add_trade(EM, MK, form(**kw)); check(label, False, "accepted")
    except ValueError as e:
        check(label, True, str(e))
rejects("a future date", date=(today + dt.timedelta(days=1)).isoformat())
rejects("a date that does not exist", date="2026-02-30")
rejects("an instrument from another market", instrument="BTC")
rejects("a side that is not CE, PE or FUT", side="XX")
rejects("a price that is not a number", entry="abc")
rejects("negative lots", lots="-1")
rejects("a note longer than the limit", notes="x" * 2001)
check("nothing was saved by the refusals", journal.load(EM, MK)["trades"] == [])

print("2. MONEY ON YOUR OWN TRADES")
tid = journal.add_trade(EM, MK, form())
e = journal.entries(EM, MK, "mine")[0]
check("lot size filled from the instrument (Nifty 65)", e["lot_size"] == 65)
check("gross = (exit - entry) x lots x lot size", e["gross"] == 30 * 2 * 65, e["gross"])
ref = round(30 * 130 - rs.net_rupees(120, 150, 130, "NSE", 0.0), 2)
check("charges estimated with the backtests' Zerodha model", e["charges_estimated"] and e["charges"] == ref, (e["charges"], ref))
check("net = gross - charges", e["net"] == round(e["gross"] - e["charges"], 2))
journal.add_trade(EM, MK, form(entry="200", exit="170", side="PE", dir="sell", charges="55"))
s = [x for x in journal.entries(EM, MK, "mine") if x["dir"] == "sell"][0]
check("a sell that falls makes money", s["gross"] == 30 * 2 * 65, s["gross"])
check("charges you typed are used as typed, not estimated", s["charges"] == 55 and not s["charges_estimated"])
journal.update_trade(EM, MK, tid, form(exit="100"))
check("editing a trade changes it", [x for x in journal.entries(EM, MK, "mine") if x["id"] == tid][0]["gross"] == -20 * 130)
try:
    journal.update_trade(EM, MK, "nope", form()); check("editing an unknown id is refused", False)
except ValueError:
    check("editing an unknown id is refused", True)

print("3. THE TOOL'S TICKETS JOIN IN")
path = trade_log.user_log_path(EM, MK)
def close_row(date, pnl, status="CLOSED — T2 hit (full target reached)", entry=100, exit_=None):
    trade_log._append({"trade_id": f"NIFTY-{date}-{pnl}", "event": "CLOSE", "date": date, "time_ist": "11:20:05",
                       "index": "NIFTY", "strike": 23400, "option_type": "CE", "entry": entry,
                       "exit": exit_ if exit_ is not None else entry + (pnl or 0) / 75, "status": status,
                       "pnl": pnl, "lot_size": 75, "lots": 1}, path=path)
d1, d2 = (today - dt.timedelta(days=2)).isoformat(), (today - dt.timedelta(days=1)).isoformat()
close_row(d1, 1500.0); close_row(d2, -750.0); close_row(d2, None)
close_row(d2, 900.0, status="CLOSED — the tool stopped while this was open")
tool = journal.entries(EM, MK, "tool")
check("closed tickets with a money figure are included", len(tool) == 2, len(tool))
check("tickets the tool never saw finish are left out", all("tool stopped" not in (t["status"] or "") for t in tool))
check("all = mine + tool, oldest first", len(journal.entries(EM, MK)) == 4 and [x["date"] for x in journal.entries(EM, MK)] == sorted(x["date"] for x in journal.entries(EM, MK)))

print("4. STATISTICS")
S = journal.summarize(journal.entries(EM, MK))
st, days = S["stats"], S["days"]
pn = [x["gross"] for x in journal.entries(EM, MK)]
check("trades and total", st["trades"] == 4 and st["gross"] == round(sum(pn), 2), (st["trades"], st["gross"]))
check("win rate", st["win_rate"] == round(100 * sum(p > 0 for p in pn) / 4, 1), st["win_rate"])
wins, losses = [p for p in pn if p > 0], [p for p in pn if p < 0]
check("profit factor", st["profit_factor"] == round(sum(wins) / -sum(losses), 2), st["profit_factor"])
check("daily totals add up", abs(sum(d["gross"] for d in days.values()) - sum(pn)) < 0.01)
eq = peak = dd = 0
for p in pn:
    eq += p; peak = max(peak, eq); dd = max(dd, peak - eq)
check("max drawdown on the running total", st["max_drawdown"] == round(dd, 2), st["max_drawdown"])
check("best and worst day", st["best_day"]["gross"] == max(d["gross"] for d in days.values()) and st["worst_day"]["gross"] == min(d["gross"] for d in days.values()))
check("green and red days", st["green_days"] + st["red_days"] <= len(days))
check("by instrument", st["by_instrument"]["NIFTY"]["trades"] == 4)
check("empty journal: no crash, zeros", journal.summarize([])["stats"]["trades"] == 0)

print("5. NOTES, DELETES, SEPARATE MARKETS")
journal.set_note(EM, MK, d1, "Chased the open. Waited for the range next time and it paid.")
check("a day note is kept", journal.load(EM, MK)["notes"][d1].startswith("Chased"))
journal.set_note(EM, MK, d1, "")
check("an empty note removes it", d1 not in journal.load(EM, MK)["notes"])
journal.delete_trade(EM, MK, tid)
check("deleting removes only that trade", len(journal.entries(EM, MK, "mine")) == 1)
check("the crypto journal is a different file", journal.load(EM, "crypto")["trades"] == [] and journal._path(EM, "crypto") != journal._path(EM, MK))
check("Bitcoin trades take BTC and have no rupee charges", journal.entries(EM, "crypto", "mine") == [] and journal.estimate_charges("BTC", 100, 110, 1) is None)
check("the file sits in the account's private folder", os.sep + "users" + os.sep in journal._path(EM, MK))

print("6. RISK FROM YOUR OWN TRADES")
import math, statistics
few = journal.risk([{"date": "2026-09-01", "gross": 100.0}] * 5)
check("under 20 trades it declines to estimate", few["enough"] is False and "sharpe" not in few)
rows, vals = [], [300, -200, 150, -400, 250, 100, -150, 500, -300, 200, 50, -100, 350, -250, 120, 80, -60, 400, -500, 220, 90, -180]
for k, v in enumerate(vals):
    rows.append({"date": f"2026-08-{k + 1:02d}", "gross": float(v)})       # one trade a day, so daily = per trade
R = journal.risk(rows)
m, sd = statistics.mean(vals), statistics.stdev(vals)
check("Sharpe = mean / sd of daily P&L x sqrt(252)", R["sharpe"] == round(m / sd * math.sqrt(252), 2), R["sharpe"])
srt = sorted(vals)
k = (len(srt) - 1) * 0.05; lo = math.floor(k)
check("1-in-20 bad day is the 5th percentile of days", R["var95_day"] == round(srt[lo] + (srt[lo + 1] - srt[lo]) * (k - lo), 2), R["var95_day"])
check("the average of the worst 5% of days", R["es95_day"] == round(sum(srt[:math.ceil(len(srt) * .05)]) / math.ceil(len(srt) * .05), 2), R["es95_day"])
mc = R["monte_carlo"]
check("Monte Carlo range is ordered and centred near 100 x the average trade",
      mc["total_p5"] < mc["total_median"] < mc["total_p95"] and abs(mc["total_median"] - 100 * m) < 3 * sd * 10 ** .5, mc)
check("same trades, same answer (seeded)", journal.risk(rows)["monte_carlo"] == mc)
check("drawdown estimates are positive and ordered", 0 < mc["drawdown_median"] <= mc["drawdown_p95"])
check("summarize carries the risk block", "risk" in journal.summarize(rows))

print()
print("JOURNAL TEST PASSED" if not fails else f"JOURNAL TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
