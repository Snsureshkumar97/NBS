"""The overnight warning: the right nights, the right window, the right money."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import btst

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
at = lambda y, m, d, h, mi: dt.datetime(y, m, d, h, mi, tzinfo=IST)
fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)
path = os.path.expanduser("~/trading-tool-logs/btst_study.json")
study = json.load(open(path)) if os.path.exists(path) else None

print("NIGHTS TO THE NEXT SESSION")
o = btst.overnight(at(2026, 9, 9, 15, 10), "NIFTY", "CE", "2026-09-15", -2000.0, study)
check("Wednesday 15:10 -> Thursday 09:15, one night", o["calendar_nights"] == 1 and o["next_open"].startswith("Thu 10 Sep"), o)
check("decay = daily decay x 18h05m", o["decay"] == round(2000 * (18 * 60 + 5) / 1440), o["decay"])
o = btst.overnight(at(2026, 9, 11, 15, 0), "NIFTY", "PE", "2026-09-15", -2000.0, study)
check("Friday before the Ganesh Chaturthi Monday -> Tuesday 09:15, four nights",
      o["calendar_nights"] == 4 and o["next_open"].startswith("Tue 15 Sep"), o["next_open"])
check("and the decay counts all of it", abs(o["decay"] - 2000 * o["days"]) <= 10 and o["days"] > 3.7, (o["decay"], o["days"]))

print("WHEN IT SHOWS")
for label, when, want in (("14:29, before the last hour", at(2026, 9, 9, 14, 29), False),
                          ("14:30", at(2026, 9, 9, 14, 30), True),
                          ("15:25, the closing auction", at(2026, 9, 9, 15, 25), True),
                          ("15:40", at(2026, 9, 9, 15, 40), True),
                          ("15:41, after the close", at(2026, 9, 9, 15, 41), False),
                          ("Saturday", at(2026, 9, 12, 15, 0), False),
                          ("the holiday itself", at(2026, 9, 14, 15, 0), False)):
    check(label, btst.overnight(when, "NIFTY", "CE", "2026-09-15", -1000.0, study)["show"] is want)

print("EXPIRY DAY")
o = btst.overnight(at(2026, 9, 15, 15, 0), "NIFTY", "CE", "2026-09-15", -5000.0, study)
check("the contract expires today: flagged, no overnight decay figure", o["expires_today"] and o["decay"] is None, o)

print("THE EVIDENCE")
if study:
    o = btst.overnight(at(2026, 9, 9, 15, 0), "NIFTY", "CE", "2026-09-15", -1000.0, study)
    ref = study["results"]["NIFTY"]["always_ce"]["CE"]["oos"]
    check("CE ticket: buying a CE into every Nifty close, held-out year", o["baseline"]["avg"] == ref["avg"] and o["baseline"]["n"] == ref["n"], o["baseline"])
    o = btst.overnight(at(2026, 9, 9, 15, 0), "SENSEX", "PE", "2026-09-15", -1000.0, study)
    check("PE ticket on Sensex: the PE figure for Sensex", o["baseline"]["avg"] == study["results"]["SENSEX"]["always_pe"]["PE"]["oos"]["avg"])
else:
    print("  (no saved study on this machine - skipped)")
check("no study: no evidence line, no crash", btst.overnight(at(2026, 9, 9, 15, 0), "NIFTY", "CE", "2026-09-15", -1000.0, None)["baseline"] is None)
check("no decay figure available: no crash", btst.overnight(at(2026, 9, 9, 15, 0), "NIFTY", "CE", "2026-09-15", None, study)["decay"] is None)

print()
print("OVERNIGHT TEST PASSED" if not fails else f"OVERNIGHT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
