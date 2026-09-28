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
def open_row(date, trade_id, time_ist="09:31:40", tracked_on="premium", strike=23400):
    trade_log._append({"trade_id": trade_id, "event": "OPEN", "date": date, "time_ist": time_ist,
                       "index": "NIFTY", "strike": strike, "option_type": "CE", "entry": 100,
                       "status": "OPEN", "lot_size": 75, "lots": 1, "tracked_on": tracked_on}, path=path)
def close_row(date, pnl, status="CLOSED — T2 hit (full target reached)", entry=100, exit_=None,
              trade_id=None, open_at=None, tracked_on="premium", strike=23400):
    tid = trade_id or f"NIFTY-{date}-{pnl}"
    if open_at:
        open_row(date, tid, open_at, tracked_on, strike)
    trade_log._append({"trade_id": tid, "event": "CLOSE", "date": date, "time_ist": "11:20:05",
                       "index": "NIFTY", "strike": strike, "option_type": "CE", "entry": entry,
                       "exit": exit_ if exit_ is not None else entry + (pnl or 0) / 75, "status": status,
                       "pnl": pnl, "lot_size": 75, "lots": 1, "tracked_on": tracked_on}, path=path)
d1, d2 = (today - dt.timedelta(days=2)).isoformat(), (today - dt.timedelta(days=1)).isoformat()
close_row(d1, 1500.0, trade_id="d1-a", open_at="09:31:40")
close_row(d2, -750.0, trade_id="d2-a"); close_row(d2, None, trade_id="d2-b")
close_row(d2, 900.0, status="CLOSED — the tool stopped while this was open", trade_id="d2-c")
tool = journal.entries(EM, MK, "tool")
check("closed tickets with a money figure are included", len(tool) == 2, len(tool))
check("tickets the tool never saw finish are left out", all("tool stopped" not in (t["status"] or "") for t in tool))
check("all = mine + tool, oldest first", len(journal.entries(EM, MK)) == 4 and [x["date"] for x in journal.entries(EM, MK)] == sorted(x["date"] for x in journal.entries(EM, MK)))

print("3b. ENTRY TIME, BESIDE THE CLOSE TIME - FROM THE OPEN ROW THE CLOSE ROW ITSELF NEVER CARRIES")
tline = [t for t in tool if t["id"] == "d1-a"][0]
check("the close time is unchanged - the CLOSE row's own time_ist", tline["time"] == "11:20", tline["time"])
check("the entry time comes from the matching OPEN row, not the CLOSE row's", tline["entry_time"] == "09:31", tline["entry_time"])
untied = [t for t in tool if t["id"] == "d2-a"][0]
check("a closed ticket with no matching OPEN row on file has no entry time - not a guess, not a crash",
      untied["entry_time"] is None, untied["entry_time"])
check("a trade you typed in yourself has no entry time either - the form only ever asked for one",
      journal.entries(EM, MK, "mine")[0].get("entry_time") is None)

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

print("7. THE STRIKE'S OWN DAY RANGE - record_options.py's saved file, not the index's")
import gzip
OH = tempfile.mkdtemp()
journal.OPTION_HISTORY_DIR = OH               # never the real ~/trading-tool-logs/option_history
journal._STRIKE_RANGE_CACHE.clear()
d3 = (today - dt.timedelta(days=4)).isoformat()
def opt_history(date, rows):
    header = "ts,kind,index,symbol,expiry,strike,opt,open,high,low,close,volume,oi\n"
    body = "".join(f"2026-01-01 09:{15+i}:00,{r['kind']},{r['index']},X,2026-01-01,{r.get('strike','')},{r.get('opt','')},"
                   f"{r['open']},{r['high']},{r['low']},{r['close']},1000,500\n" for i, r in enumerate(rows))
    with gzip.open(os.path.join(OH, f"{date}.csv.gz"), "wt") as fh:
        fh.write(header + body)
opt_history(d3, [
    {"kind": "IDX", "index": "NIFTY", "open": 23400, "high": 23450, "low": 23380, "close": 23410},   # not an option - ignored
    {"kind": "OPT", "index": "NIFTY", "strike": 23400, "opt": "CE", "open": 100, "high": 145, "low": 92, "close": 140},
    {"kind": "OPT", "index": "NIFTY", "strike": 23400, "opt": "CE", "open": 140, "high": 168, "low": 138, "close": 150},   # a second candle, same contract
    {"kind": "OPT", "index": "NIFTY", "strike": 23400, "opt": "PE", "open": 90, "high": 95, "low": 60, "close": 65},       # the put at the same strike - a different contract
    {"kind": "OPT", "index": "BANKNIFTY", "strike": 23400, "opt": "CE", "open": 1, "high": 999, "low": 1, "close": 1},     # a different index, same strike number
    {"kind": "OPT", "index": "BTC", "strike": 23400, "opt": "CE", "open": 1, "high": 777, "low": 1, "close": 1},          # never real (record_options.py never writes BTC) - here only to prove the gate, not the data's absence, is what blocks it
])
check("the day high/low is the MAX high and MIN low across every candle of that ONE contract",
      journal.strike_day_range("NIFTY", 23400, "CE", d3) == (168.0, 92.0), journal.strike_day_range("NIFTY", 23400, "CE", d3))
check("the put at the same strike is its own contract, not merged with the call",
      journal.strike_day_range("NIFTY", 23400, "PE", d3) == (95.0, 60.0))
check("another index sharing the same strike NUMBER is not this one", journal.strike_day_range("BANKNIFTY", 23400, "CE", d3) == (999.0, 1.0))
check("a strike never recorded that day: unknown, not zero", journal.strike_day_range("NIFTY", 24000, "CE", d3) == (None, None))
check("a day with no file at all (today, before record_options.py has run; a holiday; the future): unknown",
      journal.strike_day_range("NIFTY", 23400, "CE", today.isoformat()) == (None, None))
check("Bitcoin has none of this - Delta's own history is not archived this way, and the gate blocks it "
      "even when a BTC row exists in the file (proves the exchange check, not just absent data)",
      journal.strike_day_range("BTC", 23400, "CE", d3) == (None, None))
check("a ticket with no strike, or no real side, asks nothing", journal.strike_day_range("NIFTY", None, "CE", d3) == (None, None)
      and journal.strike_day_range("NIFTY", 23400, None, d3) == (None, None))
seen_before = dict(journal._STRIKE_RANGE_CACHE)
journal.strike_day_range("NIFTY", 23400, "CE", d3)
check("asking again re-reads nothing - the file's mtime/size have not changed", journal._STRIKE_RANGE_CACHE == seen_before)
opt_history(d3, [{"kind": "OPT", "index": "NIFTY", "strike": 23400, "opt": "CE", "open": 1, "high": 500, "low": 1, "close": 1}])
check("but a genuinely CHANGED file (its size differs) IS read again", journal.strike_day_range("NIFTY", 23400, "CE", d3) == (500.0, 1.0))

print("8. IT REACHES A CLOSED TICKET TRACKED ON ITS LIVE PREMIUM, AND A MANUAL CE/PE TRADE - NOT AN INDEX-TRACKED ONE")
d4 = (today - dt.timedelta(days=5)).isoformat()
opt_history(d4, [{"kind": "OPT", "index": "NIFTY", "strike": 23400, "opt": "CE", "open": 100, "high": 260, "low": 80, "close": 250}])
close_row(d4, 3000.0, trade_id="d4-premium", tracked_on="premium")
close_row(d4, -300.0, trade_id="d4-index", tracked_on="index")
tool2 = journal.entries(EM, MK, "tool")
prem = [t for t in tool2 if t["id"] == "d4-premium"][0]
idx = [t for t in tool2 if t["id"] == "d4-index"][0]
check("a ticket tracked on its live premium gets the strike's own day range", (prem["strike_day_high"], prem["strike_day_low"]) == (260.0, 80.0))
check("one tracked on the index instead does not - it has no premium to look up", idx["strike_day_high"] is None and idx["strike_day_low"] is None)
journal.add_trade(EM, MK, form(date=d4, strike="23400", side="CE", entry="100", exit="200"))
mine2 = [t for t in journal.entries(EM, MK, "mine") if t["date"] == d4][0]
check("a trade you typed in yourself, CE/PE with a strike, gets it too - you paid a real premium the same as a tracked ticket",
      (mine2["strike_day_high"], mine2["strike_day_low"]) == (260.0, 80.0))
journal.add_trade(EM, MK, form(date=d4, side="FUT", strike="", entry="23400", exit="23500"))
mine3 = [t for t in journal.entries(EM, MK, "mine") if t["side"] == "FUT"][0]
check("a futures trade has no strike to look up - none is offered, none is guessed",
      mine3["strike_day_high"] is None and mine3["strike_day_low"] is None)

print("9. THE DAY TABLE ITSELF RENDERS THE NEW COLUMNS - RUN FOR REAL IN NODE")
import shutil, subprocess
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own function", False, "install node to run this section")
else:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
    fa = src.index("function jdayPaint(")
    fb = src.index("\n}", fa) + 2
    prog = r"""
const assert = require("assert");
const num=(v,d=2)=>v===null||v===undefined||isNaN(v)?"—":
  Number(v).toLocaleString("en-IN",{minimumFractionDigits:d,maximumFractionDigits:d});
const esc=s=>String(s==null?"":s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const money=(v,signed)=>{
  const n = Math.abs(Math.round(v)).toLocaleString("en-IN");
  const sign = signed === false ? "" : (v >= 0 ? "+" : "−");
  return sign + "₹" + n;
};
const JN_M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const jdate = iso => { const d = new Date(iso + "T00:00:00Z"); return `${d.getUTCDate()} ${JN_M[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const jcol = v => v > 0 ? "var(--up)" : v < 0 ? "var(--down)" : "var(--ink-2)";

// A tiny fake DOM: just enough for jdayPaint to read/write against.
class El { constructor(){ this.style = {}; this._html = ""; this._text = ""; this._value = ""; } set innerHTML(v){ this._html = v; } get innerHTML(){ return this._html; } set textContent(v){ this._text = v; } get textContent(){ return this._text; } set value(v){ this._value = v; } get value(){ return this._value; } scrollIntoView(){} }
const ELS = {jdaycard: new El(), jdaytitle: new El(), jdaytbl: new El(), jdaynote: new El()};
const $ = id => ELS[id];
const document = { activeElement: null };
let JN_DAY = "2026-09-25";

""" + src[fa:fb] + r"""

// A closed tool ticket tracked on its live premium: an entry time, a
// different closing time, and a strike range that is known.
const d = {entries: [{
  date: JN_DAY, source: "tool", gross: 4200, net: 3950, charges: 250, cost: 6500,
  instrument: "NIFTY", strike: 25000, side: "CE", entry_time: "09:31", time: "11:20",
  entry: 108.5, exit: 175.25, strike_day_high: 260.4, strike_day_low: 80.15, lots: 1, status: "CLOSED"
}], notes: {}};
jdayPaint(d);
let html = $("jdaytbl").innerHTML;
assert.ok(html.includes("<th>Entered</th>"), "header names the entry-time column: " + html);
assert.ok(html.includes("<th>Closed</th>"), "header names the exit-time column, separately: " + html);
assert.ok(html.includes("<th>Strike range</th>"), "header names the strike day-range column: " + html);
assert.ok(html.includes("<td>09:31</td>"), "the entry time cell shows the OPEN row's own time: " + html);
assert.ok(html.includes("<td>11:20</td>"), "the closing time cell still shows the CLOSE row's own time, unchanged: " + html);
assert.ok(html.includes("<td>80.15 – 260.40</td>"), "the strike range cell reads low – high, via num(): " + html);

// No strike range known (an index-tracked ticket, crypto, or an unrecorded
// day) - the cell reads an em dash, never a blank or a stray "null".
const d2 = {entries: [{
  date: JN_DAY, source: "tool", gross: 100, net: 90, charges: 10, cost: 500,
  instrument: "NIFTY", strike: 25000, side: "CE", entry_time: "09:31", time: "10:00",
  entry: 100, exit: 101, strike_day_high: null, strike_day_low: null, lots: 1, status: "CLOSED"
}], notes: {}};
jdayPaint(d2);
html = $("jdaytbl").innerHTML;
assert.ok(html.includes("<td>—</td>"), "an unknown strike range renders as an em dash, not blank or null: " + html);

// Only ONE side known (should not happen in practice - the pair always comes from the same
// lookup - but the cell must guard against it defensively rather than print a broken half-range).
const d2b = {entries: [{
  date: JN_DAY, source: "tool", gross: 100, net: 90, charges: 10, cost: 500,
  instrument: "NIFTY", strike: 25000, side: "CE", entry_time: "09:31", time: "10:00",
  entry: 100, exit: 101, strike_day_high: 150.0, strike_day_low: null, lots: 1, status: "CLOSED"
}], notes: {}};
jdayPaint(d2b);
html = $("jdaytbl").innerHTML;
assert.ok(!html.includes("150.00"), "half-known range (high but no low) must not leak a lone number into the cell: " + html);
assert.ok(html.includes("<td>—</td>"), "...it falls back to the em dash instead: " + html);

// A trade with no matching OPEN row (e.g. one from before this feature
// shipped) shows an empty entry-time cell, not "undefined" or a crash.
const d3 = {entries: [{
  date: JN_DAY, source: "mine", gross: 50, net: 45, charges: 5, cost: 200,
  instrument: "NIFTY", strike: 25000, side: "CE", entry_time: null, time: "10:00",
  entry: 100, exit: 100.5, strike_day_high: null, strike_day_low: null, lots: 1, notes: ""
}], notes: {}};
jdayPaint(d3);
html = $("jdaytbl").innerHTML;
assert.ok(html.includes("<td></td><td>10:00</td>"), "no entry_time: the cell is simply empty, right before the known closing time: " + html);

console.log("ok");
"""
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("jdayPaint renders Entered/Closed/Strike range columns correctly, and copes with unknowns",
          r.returncode == 0 and r.stdout.strip() == "ok", (r.stderr or r.stdout)[-1000:])

print()
print("JOURNAL TEST PASSED" if not fails else f"JOURNAL TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
