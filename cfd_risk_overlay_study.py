#!/usr/bin/env python3
"""
cfd_risk_overlay_study.py — risk control on the 87% BTC rule: position size, and circuit breakers
================================================================================
The user, 4 Oct 2026: "think like a trader with 20 years of experience ... make this tool make profits and
less losses ... just do it". The rule's edge is unproven and thin, so first what decides survival: how much
each trade risks, and whether to stop after losses. The live rule exactly as cfd_monthly_live_rules.py
(real-tick outcomes, $10 spread or more, the spread check, swap, one at a time).

PART 1 - SIZE (same trades; only how much each risks). From the demo's $5,000, compounding:
  fixed 0.25 lot (today) | risk 1% of the account per trade | risk 2% per trade
  (risk = the stop distance, 3 x ATR, plus the spread, x the BTC held; rounded DOWN to Exness's 0.01, min 0.01)
PART 2 - CIRCUIT BREAKERS (they change which trades are taken), at 0.25 lot:
  stop for the IST day after 1 loss | after 2 losses | pause 12 hours after any loss
  KEEP only if more profit than today in BOTH periods.

    python3 cfd_risk_overlay_study.py
"""
import os
import sys

import numpy as np
import pandas as pd

FLOOR = 10.0
START = 5000.0
COOLDOWN_NS = 20 * 60 * 10 ** 9


def load():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_rules
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import config
    import exness_data as ed
    plan = config.CFD_RULES["BTC"]
    full = ed.load("BTCUSD")
    a = cfd_rules.compute(full[["Open", "High", "Low", "Close", "Volume"]])
    side = np.where((a["rsi2"] != 0) & a["adx25"], a["rsi2"], 0).astype(np.int64)
    O = np.load(os.path.join(ed._dir(), "outcomes_close_BTCUSD.npz"))
    k = list(O["stops"]).index(plan["stop_atr"])
    j = int(np.argmin(np.abs(O["targets"] - plan["target_r"])))
    spc = full["spread_close"].to_numpy()
    eff = np.maximum(spc, FLOOR)
    side = np.where(np.round(eff / (plan["target_r"] * plan["stop_atr"] * a["atr"]), 9) < plan["max_spread_share"], side, 0)
    side = np.where(O["ok"], side, 0)
    entry = full.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
    sd = (side < 0).astype(np.int64)
    rows = np.arange(len(full))
    net = O["net"][rows, sd, k, j].astype(float) - (eff - spc)
    ext = O["ext"][rows, sd, k, j]
    long_sw, _, triple = cts.SWAP["BTCUSD"]
    rolls, cumw = vs.rollover_weights(entry[0], entry[-1] + 3 * 86400 * 10 ** 9, triple)
    nights = cumw[np.searchsorted(rolls, ext, side="right") - 1] - cumw[np.searchsorted(rolls, entry, side="right") - 1]
    net = net + np.where(side > 0, nights * long_sw, 0.0)
    risk = plan["stop_atr"] * a["atr"] + eff                            # $ lost per BTC at the stop, with the spread
    return {"side": side, "net": net, "ext": ext, "entry": entry, "risk": risk, "split": vs.SPLIT, "stats": vs.stats}


def run(D, pause=None):
    """One at a time, 20 min after an exit; pause = None | ("day", n losses) | ("hours", h)."""
    side, net, ext, entry = D["side"], D["net"], D["ext"], D["entry"]
    out, free = [], -1
    day_losses, blocked_until = {}, -1
    ist_day = lambda ns: (ns + 330 * 60 * 10 ** 9) // (86400 * 10 ** 9)
    for i in np.flatnonzero(side != 0):
        if np.isnan(net[i]) or entry[i] < free or entry[i] < blocked_until:
            continue
        if pause and pause[0] == "day" and day_losses.get(ist_day(entry[i]), 0) >= pause[1]:
            continue
        out.append(i)
        free = ext[i] + COOLDOWN_NS
        if net[i] < 0:
            dkey = ist_day(ext[i])
            day_losses[dkey] = day_losses.get(dkey, 0) + 1
            if pause and pause[0] == "hours":
                blocked_until = ext[i] + int(pause[1] * 3600 * 10 ** 9)
    return np.array(out, dtype=np.int64)


def main():
    D = load()
    base = run(D)
    ins = D["entry"][base] < D["split"]
    print("PART 1 - SIZE: the same", len(base), "trades, from a $5,000 account, compounding")
    print(f"  {'sizing':26s} {'end balance':>12s} {'worst drop':>11s} {'worst month':>12s} {'avg risk/trade':>15s} {'months down':>12s}")
    months = pd.to_datetime(D["entry"][base], utc=True).tz_convert("Asia/Kolkata").strftime("%Y-%m")
    for name, mode in (("fixed 0.25 lot (today)", ("lot", 0.25)), ("risk 1% per trade", ("pct", 0.01)),
                       ("risk 2% per trade", ("pct", 0.02))):
        eq, peak, dd, pnl, risks = START, START, 0.0, [], []
        for i in base:
            lots = mode[1] if mode[0] == "lot" else max(0.01, np.floor(eq * mode[1] / D["risk"][i] * 100) / 100)
            p = lots * D["net"][i]
            risks.append(100 * lots * D["risk"][i] / eq)
            eq += p
            pnl.append(p)
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak)
        m = pd.Series(pnl, index=months).groupby(level=0).sum()
        mstart = pd.Series(np.concatenate([[START], START + np.cumsum(pnl)[:-1]]), index=months).groupby(level=0).first()
        print(f"  {name:26s} {eq:>12,.0f} {100 * dd:>10.1f}% {100 * (m / mstart).min():>11.1f}% "
              f"{np.mean(risks):>14.2f}% {int((m < 0).sum()):>6d} of {len(m)}")
    print("\nPART 2 - CIRCUIT BREAKERS at 0.25 lot ($ per 0.25 BTC; KEEP = more profit than today in BOTH periods)")
    print(f"  {'rule':34s} {'IN-SAMPLE   n  win%     net  maxDD':>36s}   {'HELD-OUT   n  win%     net  maxDD':>36s}")
    res = {}
    for name, pz in (("today (no breaker)", None), ("stop for the day after 1 loss", ("day", 1)),
                     ("stop for the day after 2 losses", ("day", 2)), ("pause 12 h after a loss", ("hours", 12))):
        tr = run(D, pz)
        cells = []
        for per in ("is", "oos"):
            m_ = (D["entry"][tr] < D["split"]) if per == "is" else (D["entry"][tr] >= D["split"])
            s = D["stats"](0.25 * D["net"][tr][m_])
            res[(name, per)] = s
            cells.append(f"{s['n']:>5} {100 * s['win']:>4.0f}% {s['net']:>+7,.0f} {s['dd']:>6,.0f}")
        keep = "" if pz is None else ("  KEEP" if all(res[(name, p)]["net"] > res[("today (no breaker)", p)]["net"]
                                                      for p in ("is", "oos")) else "  drop")
        print(f"  {name:34s} {cells[0]:>36s}   {cells[1]:>36s}{keep}")


if __name__ == "__main__":
    main()
