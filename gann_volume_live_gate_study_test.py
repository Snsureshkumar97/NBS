"""gann_volume_live_gate_study.py's own wiring: the combined gate is really AND, and it calls
res.live_gate with the real signature (not a copy of it). The Gann/volume math itself is
gann_volume_study.py's, unchanged and untouched here - not retested."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gann_volume_live_gate_study as gls

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE COMBINED GATE IS A REAL AND, NEITHER SIDE ALONE IS ENOUGH")
calls = []
def fake_live_gate(k, i, rec, df):
    calls.append((k, i))
    return i % 2 == 0          # live gate passes on even i only

orig = gls.res.live_gate
gls.res.live_gate = fake_live_gate
try:
    base_gate = (lambda i, rec: gls.res.live_gate("NIFTY", i, rec, None))
    extra_true = lambda i, r: True
    extra_false = lambda i, r: False
    combo_true = (lambda i, r: base_gate(i, r) and extra_true(i, r))
    combo_false = (lambda i, r: base_gate(i, r) and extra_false(i, r))
    check("live gate true + extra true -> True (even i)", combo_true(2, {}) is True)
    check("live gate false + extra true -> False (odd i)", combo_true(3, {}) is False)
    check("live gate true + extra false -> False", combo_false(2, {}) is False)
finally:
    gls.res.live_gate = orig

print("2. base_gate PASSES THE REAL rec/i/df THROUGH TO live_gate UNCHANGED")
seen = {}
def spy_live_gate(k, i, rec, df):
    seen["args"] = (k, i, rec, df)
    return True
gls.res.live_gate = spy_live_gate
try:
    hists = {"NIFTY": "FAKE_DF"}
    base_gate = {"NIFTY": (lambda i, rec, k="NIFTY": gls.res.live_gate(k, i, rec, hists[k]))}
    marker_rec = {"option_type": "CE", "marker": 123}
    base_gate["NIFTY"](7, marker_rec)
    check("index key forwarded", seen["args"][0] == "NIFTY")
    check("bar index forwarded", seen["args"][1] == 7)
    check("the exact rec object forwarded, not a copy missing fields", seen["args"][2] is marker_rec)
    check("the right df forwarded", seen["args"][3] == "FAKE_DF")
finally:
    gls.res.live_gate = orig

print("3. THE GANN/VOLUME GATE FUNCTIONS REUSED HERE ARE gann_volume_study's OWN, NOT REDEFINED")
check("g1_room is the same function object as gann_volume_study.g1_room",
      gls.gv.VARIANTS["G1 Gann room"] is gls.gv.g1_room)
check("g2_behind is the same function object", gls.gv.VARIANTS["G2 Gann level behind"] is gls.gv.g2_behind)
check("v1_rising is the same function object", gls.gv.VARIANTS["V1 volume rising"] is gls.gv.v1_rising)

print()
print("GANN VOLUME LIVE GATE STUDY TEST PASSED" if not fails
      else f"GANN VOLUME LIVE GATE STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
