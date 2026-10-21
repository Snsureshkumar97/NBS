#!/usr/bin/env python3
"""The Central Pivot Range (cpr.py) and how the page shows it (the user, 21 Oct 2026: "what is CPR", "show it and give
simple explanation", "test it as well"). The formula, narrow / normal / wide, today's against the next session's, where
the price is, the plain words - which say what cpr_study.py found, not only what traders say. No network."""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd

import cpr
import feeds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE FORMULA")
p, lo, hi = cpr.band(23200.0, 22900.0, 23100.0)
check("P = (H+L+C)/3, BC = (H+L)/2, TC = 2P - BC: 23,066.67 / 23,050 / 23,083.33", round(p, 2) == 23066.67
      and lo == 23050.0 and round(hi, 2) == 23083.33, (p, lo, hi))
p, lo, hi = cpr.band(23200.0, 22900.0, 22950.0)
check("a close below the middle: TC (22,983.33) under BC (23,050) - the band still runs low to high",
      lo < hi and round(lo, 2) == 22983.33 and hi == 23050.0, (lo, hi))

print("2. NARROW, NORMAL, WIDE")
base = [(dt.date(2026, 10, d), 23200.0, 22900.0, 23150.0) for d in range(1, 12)]    # close near the top: a wide band
w0 = cpr.levels(base)["width"]
narrow = base + [(dt.date(2026, 10, 12), 23200.0, 22900.0, 23050.0)]                # close in the middle: a narrow band
lv = cpr.levels(narrow, now=23100.0)
check("a much tighter band than the last ten: narrow", lv["kind"] == "narrow" and lv["ratio"] <= 0.6, lv)
check("the price over its top: above", lv["where"] == "above")
check("the same band as the last ten: normal", cpr.levels(base)["kind"] == "normal")
wide = [(dt.date(2026, 10, d), 23200.0, 22900.0, 23060.0) for d in range(1, 12)] + [(dt.date(2026, 10, 12), 23200.0, 22900.0, 23190.0)]
check("a much wider band than the last ten: wide", cpr.levels(wide)["kind"] == "wide")
check("fewer than four sessions: nothing", cpr.levels(base[:3]) is None)
b0 = cpr.levels(base)                                                             # 23,050 - 23,116.67
check("inside / below", cpr.levels(base, now=23100.0)["where"] == "inside" and cpr.levels(base, now=22000.0)["where"] == "below",
      (b0["bc"], b0["tc"]))

print("3. TODAY'S, OR THE NEXT SESSION'S (the live feed)")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
idx = []
vals = []
for d in pd.bdate_range("2026-10-01", "2026-10-21"):
    for m in range(0, 375, 15):
        ts = pd.Timestamp(d.year, d.month, d.day, 9, 15, tz="Asia/Kolkata") + pd.Timedelta(minutes=m)
        idx.append(ts)
        base_px = 23000 + d.day * 10
        vals.append((base_px + 50 + (m % 30), base_px - 50 - (m % 45), base_px + (m % 20)))
df = pd.DataFrame(vals, index=pd.DatetimeIndex(idx), columns=["High", "Low", "Close"])


class F:
    _cpr_cache = {}


f = F()
f._cpr = feeds.Feed._cpr.__get__(f)
real_now = feeds.now_ist
feeds.now_ist = lambda: dt.datetime(2026, 10, 21, 11, 0, tzinfo=IST)
c = f._cpr("NIFTY", df, 23250.0)
check("11:00 on a trading day: today's CPR, built from the session before", c and c["for"] == "today"
      and c["session"] == "2026-10-20", c)
feeds.now_ist = lambda: dt.datetime(2026, 10, 21, 16, 0, tzinfo=IST)
f._cpr_cache.clear()
c = f._cpr("NIFTY", df, 23250.0)
check("after 15:30: the next session's, built from today", c and c["for"] == "next" and c["session"] == "2026-10-21", c)
feeds.now_ist = lambda: dt.datetime(2026, 10, 24, 11, 0, tzinfo=IST)        # a Saturday
f._cpr_cache.clear()
c = f._cpr("NIFTY", df, 23250.0)
check("a Saturday: the next session's", c and c["for"] == "next", c)
feeds.now_ist = real_now
check("crypto (never closes): none", f._cpr("BTC", df, 80000.0) is None)

print("4. THE PLAIN WORDS (node)")
src = open(os.path.join(HERE, "web_server.py")).read()
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
else:
    n0 = src.index("const num=(v,d=2)")
    n1 = src.index("\n", src.index("const esc=s=>", n0)) + 1
    fn = src[src.index("function cprSay(c){"):src.index("const CPRWORD")]
    cases = {"narrow": dict(lv, where="above", **{"for": "today"}),
             "wide": dict(cpr.levels(wide, now=23000.0), **{"for": "next"}),
             "normal": dict(cpr.levels(base, now=23060.0), **{"for": "today"})}
    prog = src[n0:n1] + fn + "\nconst C = " + json.dumps(cases) + ";\nconst o = {};\nfor (const k in C) o[k] = cprSay(C[k]);\n" \
        "o.none = cprSay(null);\nconsole.log(JSON.stringify(o));"
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        o = {}
        print(r.stdout, r.stderr)
    check("narrow: what traders expect AND what our test found", "is narrow" in o.get("narrow", "")
          and "Many traders expect a big trending day" in o.get("narrow", "")
          and "in our 3-year test, narrow-CPR days moved about the same" in o.get("narrow", ""), o.get("narrow"))
    check("wide, for the next session: the same honesty", o.get("wide", "").startswith("For the next session the CPR is wide")
          and "wide-CPR days actually moved a little more" in o.get("wide", ""), o.get("wide"))
    check("normal: no special hint", "normal width" in o.get("normal", "") and "no special hint" in o.get("normal", ""))
    check("where the price is, in words", "buyers are in control" in o.get("narrow", "")
          and "inside the CPR" in o.get("normal", ""))
    check("no data: nothing", o.get("none") == "")

print("5. ON THE PAGE")
home = src[src.index('<section class="pane on" data-pane="home">'):src.index("</section>", src.index('data-pane="home"'))]
check("off the Dashboard (21 Oct 2026: \"draw it on the chart and remove from dashboard\")",
      'id="kdcpr"' not in home and "cprDash(" not in src)
check("on the chart: a shaded band (a baseline series from the CPR's bottom to its top) over the session it is for",
      "s.cpr = chart.addSeries(LW.BaselineSeries" in src and "function chCpr(bars, C){" in src
      and "try{ chCpr(bars, C); }catch(e){}" in src and 'c.for === "today" && CH.tf !== "1d"' in src)
check("its top, bottom and pivot as labelled lines on the price axis ('next ...' after the close)",
      '[cp && cp.tc, nx + "CPR top", C.accent, "dot"]' in src and '[cp && cp.pivot, nx + "pivot", C.ink3, "dot"]' in src)
check("the plain words under the chart", 'id="cvcpr"' in src and "esc(cprSay(c))} The shaded band on the chart is the CPR" in src)
check("a rebuilt chart (a theme change) redraws the band", "cprSig: null});" in src)
check("the Signal page: its own box after the sellers box", 'id="sigcpr"' in src and "  sigSellers(r);\n  sigCpr(r);" in src)
check("the Research levels table: CPR and its width", '["CPR width", r =>' in src and '"cpr_kind": c_["kind"]' in src)
check("the live reading carries it", '"cpr": rec.get("cpr"),' in open(os.path.join(HERE, "feeds.py")).read())

print()
print("CPR TEST PASSED" if not fails else f"CPR TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
