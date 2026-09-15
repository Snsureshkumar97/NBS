#!/usr/bin/env python3
"""The ticket's what-if box: greeks.scenarios, checked against the model by hand."""
import math
import greeks as gk

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    cond or fails.append(name)

spot, K, iv, qty = 24500.0, 24500, 0.13, 150
t = gk.years_to_expiry(3 * 24 * 60)
prem = gk.price(spot, K, t, iv, "CE") + 1.5   # a quote a little off the model
ce = {r["key"]: r for r in gk.scenarios(spot, K, t, iv, "CE", prem, qty)}
pe_prem = gk.price(spot, K, t, iv, "PE")
pe = {r["key"]: r for r in gk.scenarios(spot, K, t, iv, "PE", pe_prem, qty)}
base = gk.price(spot, K, t, iv, "CE")

print("1. REPRICED IN FULL, FROM THE PREMIUM ON SCREEN")
up = prem + gk.price(spot * 1.005, K, t, iv, "CE") - base
check("index up 0.5%: market premium plus the model's change", abs(ce["up"]["premium"] - round(up, 2)) < 1e-9, ce["up"])
check("rupees = change x units", abs(ce["up"]["rupees"] - round((round(up, 2) - prem) * qty, 0)) <= 1.5, ce["up"]["rupees"])
dS = spot * 0.005; g = gk.greeks(spot, K, t, iv, "CE")
lin = g["delta"] * dS
check("not a straight line: differs from delta x move by about half gamma x move squared",
      abs((up - prem) - lin - 0.5 * g["gamma"] * dS * dS) < 0.25 * 0.5 * g["gamma"] * dS * dS + 0.05,
      f"full {up - prem:.2f} vs delta {lin:.2f}")
vol = prem + gk.price(spot, K, t, iv - 0.02, "CE") - base
check("volatility 2 points lower", abs(ce["vol"]["premium"] - round(vol, 2)) < 1e-9)
later = prem + gk.price(spot, K, t - 1 / 365, iv, "CE") - base
check("a day later, nothing moves", abs(ce["time"]["premium"] - round(later, 2)) < 1e-9 and ce["time"]["label"].startswith("A day later"))

print("2. DIRECTIONS")
check("call: up gains, down loses", ce["up"]["change"] > 0 > ce["down"]["change"])
check("put: up loses, down gains", pe["up"]["change"] < 0 < pe["down"]["change"])
check("volatility lower and a day later both cost, calls and puts", all(d[k]["change"] < 0 for d in (ce, pe) for k in ("vol", "time")))
check("'all three against you' is the worst row, call", ce["worst"]["change"] == min(r["change"] for r in ce.values()))
check("'all three against you' is the worst row, put", pe["worst"]["change"] == min(r["change"] for r in pe.values()))
w = pe_prem + gk.price(spot * 1.005, K, t - 1 / 365, iv - 0.02, "PE") - gk.price(spot, K, t, iv, "PE")
check("a put's worst case moves the index up, not down", abs(pe["worst"]["premium"] - round(w, 2)) < 1e-9)

print("3. EDGES")
t0 = gk.years_to_expiry(90)
cp = gk.price(24620, K, t0, iv, "CE")
e = {r["key"]: r for r in gk.scenarios(24620, K, t0, iv, "CE", cp, qty)}
check("under a day left: runs to the expiry, at intrinsic value", e["time"]["label"].startswith("At the 15:30 expiry")
      and abs(e["time"]["premium"] - 120.0) < 1e-6, e["time"])
# A quote far below the model: the model's fall is larger than the premium itself.
far = {r["key"]: r for r in gk.scenarios(spot, K, t, iv, "CE", 5.0, qty)}
check("a premium is never below zero", far["worst"]["premium"] == 0.0 and far["worst"]["change"] == -5.0
      and all(r["premium"] >= 0 for r in far.values()), far["worst"])
check("no quantity: no rupee column", all(r["rupees"] is None for r in gk.scenarios(spot, K, t, iv, "CE", prem, 0)))
check("missing inputs give nothing rather than a guess", gk.scenarios(spot, K, 0, iv, "CE", prem) == []
      and gk.scenarios(spot, K, t, None, "CE", prem) == [] and gk.scenarios(spot, K, t, iv, "FUT", prem) == [])
low = {r["key"]: r for r in gk.scenarios(spot, K, t, 0.015, "CE", gk.price(spot, K, t, 0.015, "CE"), qty)}
check("volatility never priced below 1%", low["vol"]["premium"] >= 0 and math.isfinite(low["vol"]["premium"]))

print("WHAT-IF TEST PASSED" if not fails else f"WHAT-IF TEST FAILED: {fails}")
