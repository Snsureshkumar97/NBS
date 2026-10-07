#!/usr/bin/env python3
"""The Journal drawn as figures, charts and tables (the user, 7 Oct 2026: "work on journal the numbers over there all
too messy make it look good you use tables charts design like different kind for the data"). Runs the page's own
drawing functions in node on a set of trades: the headline figures, the running total as a chart with a bar per day
(each opening its day), won/lost and win/loss as bars with the rest as rows, the result by instrument, side and hour
of entry, the day table with its charges folded under the net and a day total, and risk as figures and a range. Every
name and note a trade carries comes out escaped; an empty period says so instead of drawing nothing."""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "web_server.py")).read()

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

def fn(name):
    a = SRC.index(f"function {name}(")
    return SRC[a:SRC.index("\n}\n", a) + 3]

print("1. THE PAGE")
pane = SRC[SRC.index('<section class="pane" data-pane="journal">'):SRC.index('<section class="pane" data-pane="admin">')]
order = [pane.index(f'id="{k}"') for k in ("jovercard", "jstatscard", "jbreakcard", "jcalcard", "jdaycard", "jbookcard", "jriskcard", "jreviewcard")]
check("the overview first, then statistics beside the breakdown, the calendar full width, the day, the year, risk, the backtest",
      order == sorted(order), order)
check("the period buttons sit on the overview", pane.index('id="jscope"') < pane.index('id="jstatscard"'))
check("no text under 12px in the Journal's styles",
      not [x for x in re.findall(r"\.(?:j[a-z0-9]+|psum\.jkpi3?)[^{]*\{[^}]*font-size:(\d+)px", SRC) if int(x) < 12])

print("2. DRAWN IN NODE")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False, "install node to run this section")
else:
    lines = SRC.splitlines()
    pick = lambda start: next(l for l in lines if l.startswith(start))
    head = "\n".join([pick("const esc=s=>"), pick("const num=(v,d=2)=>"), lines[lines.index(pick("const num=(v,d=2)=>")) + 1],
                      'let CCY = "INR";', fn("ccySym"), fn("ccyLocale"), fn("money"),
                      pick("const JN_M = "), pick("const JN_MONTHS = "), pick("const jdate = "), pick("const jcol = "),
                      fn("jstats"), fn("jkc"), fn("jkv"), fn("jlines"), fn("jstatsPaint"), fn("jeqPaint"), fn("jbreakPaint"),
                      fn("jriskPaint"), fn("jdayPaint")])
    prog = r'''
let JN_SCOPE = "all", JN_MONTH = "2026-10", JN_DAY = "2026-10-06";
class El { constructor(){ this.style = {}; this.innerHTML = ""; this.textContent = ""; this.value = ""; } }
const ELS = {};
const $ = id => (ELS[id] = ELS[id] || new El());
const LBL = [new El(), new El()];
const document = {activeElement: null, querySelectorAll: q => q === ".jscopelbl" ? LBL : []};
''' + head + r'''
const assert = require("assert");
const T = (date, inst, side, entry_time, gross, extra) => Object.assign({date, instrument: inst, side, entry_time, time: entry_time,
  gross, net: gross - 50, charges: 50, charges_estimated: true, lots: 1, cost: 6000, entry: 100, exit: 100 + gross / 65,
  strike: 25000, source: "tool", status: "CLOSED — target hit", id: "t" + Math.random()}, extra || {});
const d = {market: "nse_index", entries: [
  T("2026-09-29", "NIFTY", "CE", "09:20", 1300), T("2026-09-29", "SENSEX", "PE", "13:05", -400),
  T("2026-10-01", "NIFTY", "PE", "10:40", -650), T("2026-10-06", "<b>BAD</b>", "CE", "11:15", 900,
  {source: "mine", notes: "<script>x</script>", status: ""}),
  T("2026-10-06", "NIFTY", "CE", "14:02", 260, {live: "zerodha"})], notes: {}};

jstatsPaint(d);
const kpi = $("jkpi").innerHTML, st = $("jstats").innerHTML, eq = $("jeq").innerHTML, br = $("jbreak").innerHTML;
// headline: net 1410 - 250 = 1160 after charges, the gross under it; 3 of 5 won
assert.ok(kpi.includes("<span>Net P&amp;L</span><b style=\"color:var(--up)\">+₹1,160</b>") && kpi.includes("+₹1,410 before costs"), kpi);
assert.ok(kpi.includes("<span>Win rate</span><b>60%</b>") && kpi.includes('class="jmini"><i style="width:60%">') && kpi.includes("3 won · 2 lost"), kpi);
assert.strictEqual($("jstatscope").textContent, "all time"); assert.ok(LBL.every(l => l.textContent === "all time"), "every scope label follows");
// the running total: three days, the last at +1,410, a bar per day that opens it, the selected day solid
assert.ok(eq.includes("<svg") && eq.includes("+₹1,410") && (eq.match(/<rect data-day=/g) || []).length === 3, eq.slice(0, 300));
assert.ok(eq.includes('data-day="2026-10-06"') && /data-day="2026-10-06"[^>]*opacity:1/.test(eq) && eq.includes("29 Sep 2026") && eq.includes("6 Oct 2026"), "days and dates");
// statistics: the split bar, the pair of averages, the rows
assert.ok(st.includes('<i class="up" style="width:60.0%"></i><i class="dn" style="width:40.0%"></i>') && st.includes("3 won") && st.includes("2 lost"), st);
assert.ok(st.includes("Average win") && st.includes("+₹820") && st.includes("Average loss") && st.includes("−₹525"), st);
assert.ok(st.includes("<td>Best day</td>") && st.includes("<small>6 Oct 2026</small>") && st.includes("<td>Deepest drawdown</td>"), st);
// the breakdown: NIFTY first (+910), then the hostile name (+900, escaped), then SENSEX, sides named, hours 09..14 with no gaps
assert.ok(br.indexOf("&lt;b&gt;BAD&lt;/b&gt;") > 0 && !br.includes("<b>BAD"), "an instrument name is escaped");
assert.ok(br.indexOf("<td>NIFTY") < br.indexOf("<td>&lt;b&gt;BAD") && br.indexOf("<td>&lt;b&gt;BAD") < br.indexOf("<td>SENSEX"), "sorted by result");
assert.ok(br.includes("Calls (CE)") && br.includes("Puts (PE)") && br.includes("By hour of entry"), br);
const hrs = (br.split('class="jhours"')[1] || "").match(/<span>(\d\d)<\/span>/g) || [];
assert.deepStrictEqual(hrs.map(h => h.slice(6, 8)), ["09", "10", "11", "12", "13", "14"], "an hour with nothing still has its place");

// the day: charges under the net, the reason or note under the contract (escaped), edit only on your own, a total
jdayPaint(d);
const day = $("jdaytbl").innerHTML;
assert.ok(day.includes("<th>Entry &rarr; exit</th>") && day.includes('<th title="after charges">Net</th>') && !day.includes("<th>Charges</th>"), day.slice(0, 400));
assert.ok(day.includes("&lt;script&gt;x&lt;/script&gt;") && !day.includes("<script>"), "a note is escaped");
assert.ok((day.match(/data-jedit=/g) || []).length === 1 && day.includes('est.</small>'), "edit on your own trade only; charges folded");
assert.ok(day.includes("<tfoot>") && day.includes("Day total &middot; 2 trades") && day.includes("+₹1,160") && day.includes("+₹1,060"), day.split("<tfoot>")[1]);

// risk: the ratios, the range split at zero, the rows
jriskPaint({enough: true, trades: 40, days: 20, sharpe: 1.2, sortino: -0.4, var95_day: -900, es95_day: -1300,
            monte_carlo: {trades: 100, runs: 2000, total_p5: -2000, total_p95: 8000, total_median: 3000, chance_down: 12,
                          drawdown_median: 1500, drawdown_p95: 3200}});
const rk = $("jrisk").innerHTML;
assert.ok(rk.includes("<span>Sharpe ratio</span><b style=\"color:var(--up)\">1.20</b>") && rk.includes("<span>Sortino ratio</span><b style=\"color:var(--down)\">-0.40</b>"), rk);
assert.ok(rk.includes('class="band dn"') && rk.includes('class="band up"') && rk.includes("Bad case") && rk.includes("−₹2,000") && rk.includes("+₹8,000"), rk);
jriskPaint({enough: true, trades: 40, days: 20, sharpe: 1, sortino: 1, var95_day: -1, es95_day: -1,
            monte_carlo: {trades: 100, runs: 10, total_p5: 500, total_p95: 900, total_median: 700, chance_down: 0, drawdown_median: 1, drawdown_p95: 2}});
assert.ok(!$("jrisk").innerHTML.includes("band dn") && $("jrisk").innerHTML.includes("band up"), "no red band when even the bad case is a profit");

// an empty period says so
JN_SCOPE = "month"; JN_MONTH = "2026-08";
jstatsPaint(d);
assert.ok($("jstats").innerHTML.includes("No trades this month") && $("jeq").innerHTML.includes("No trades") && $("jkpi").innerHTML === "", "empty");
console.log("ok:journal");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("headline figures, the running total with a bar per day, statistics as bars and rows, the breakdown by "
          "instrument / side / hour, the day table with a total, risk as a range - all escaped; an empty period says so",
          "ok:journal" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-1200:])

print()
print("JOURNAL LOOK TEST PASSED" if not fails else f"JOURNAL LOOK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
