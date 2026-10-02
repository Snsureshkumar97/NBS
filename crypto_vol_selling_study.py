#!/usr/bin/env python3
"""
crypto_vol_selling_study.py — can SELLING BTC options on Delta make money?
================================================================================
The user, 2 Oct 2026: "make a strategy that makes profits in btc" - after every
option-BUYING variant tested today lost after costs.

WHY SELLING
    Over these 3 years BTC moved only 78% (in-sample) / 87% (held-out) of what
    DVOL priced. Buyers pay that gap on every trade - it is a large part of why
    the tool's BTC tickets lose. The side that collects it is the seller: the
    "volatility risk premium", one of the best-documented edges in options.

PRICED AT WHAT SHORT-DATED OPTIONS REALLY TRADE AT, NOT AT DVOL
    DVOL is a 30-day vol. crypto_data.short_dated_iv() sampled REAL Deribit trades
    (157 hours, ~weekly, 12:00 UTC, near-the-money, next-day expiry): they trade at
    a median 0.89x DVOL in-sample and 0.93x held-out. Pricing a seller at full
    DVOL would overstate every credit by ~10%. Here each day's vol is DVOL (previous
    complete hour) x the TRAILING median of the last 8 measured ratios - never a
    ratio from the future.

THE TRADE (pre-declared): at 17:30 IST every day - the moment Delta's dailies
settle - SELL the next day's at-the-money options (strike to the nearest 200),
24 hours to expiry, and hold to settlement at intrinsic value. Costs: Delta's
option fee (0.01% of notional, capped at 3.5% of premium) + 18% GST on every leg
sold, half the 2% spread given up on entry, and - conservatively - the same fee
again on any leg that settles in the money. Variants:
    straddle            sell the ATM call + put
    strangle 2%         sell a call 2% above and a put 2% below (smaller credit,
                        wider break-even)
    straddle + stop     buy it back (paying fee + half the spread) if its value
                        reaches 2x the credit - caps a runaway day near one credit
    straddle, rich IV   only on days the (adjusted) implied vol is above the last
                        7 days' realised vol
RISK REPORTED, NOT JUST PROFIT: worst day, worst 7 days, max drawdown. A short
option's loss is not capped by the premium the way a bought option's is.

    python3 crypto_vol_selling_study.py
"""
import math

import numpy as np
import pandas as pd

import crypto_data as cd
import crypto_strategy_study as cs
import regime_study as rs

STEP = 200
SPREAD_RT = 0.02
SETTLE = (17, 15)                  # the 15-minute bar starting 17:15 IST closes at 17:30


def sell_vol_series(iv_dvol, samples):
    """DVOL (fraction, previous complete hour) x the trailing median of the last 8
    measured short-dated/DVOL ratios - only samples strictly before each time."""
    r = samples["ratio"].sort_index()
    trail = r.rolling(8, min_periods=3).median().shift(1)
    trail.index = trail.index.tz_convert(iv_dvol.index.tz)
    # No fill before the first 3 measurements exist: those days are skipped (NaN), rather
    # than borrowing a ratio measured later.
    scale = trail.reindex(iv_dvol.index, method="ffill")
    return iv_dvol * scale


def leg_fee(premium, spot):
    return cs.option_fee(premium, spot)            # 0.01% notional, cap 3.5% of premium, +GST


def sell_day(path_hi, path_lo, path_cl, path_t, S0, t0, exp, vol0, vols, kind="straddle",
             width=0.0, stop_mult=None):
    """One short-vol day. Returns (net $ per 1 BTC, credit, exit_reason).
    path_* are the 15-minute bars after entry up to and including the one that
    closes at expiry; vols the selling vol at each of those bars' closes."""
    T0 = (exp - t0).total_seconds() / 3600 / (365 * 24)
    if kind == "straddle":
        Kc = Kp = round(S0 / STEP) * STEP
    else:
        Kc = round(S0 * (1 + width) / STEP) * STEP
        Kp = round(S0 * (1 - width) / STEP) * STEP
    c0, p0 = rs.bs(S0, Kc, T0, vol0, True), rs.bs(S0, Kp, T0, vol0, False)
    credit = c0 + p0
    if credit < 20:
        return None
    cost_in = leg_fee(c0, S0) + leg_fee(p0, S0) + SPREAD_RT / 2 * credit
    for k in range(len(path_cl)):
        t = path_t[k]
        if t >= exp:
            S = path_cl[k]
            ci, pi = max(0.0, S - Kc), max(0.0, Kp - S)
            cost_out = (leg_fee(ci, S) if ci > 0 else 0.0) + (leg_fee(pi, S) if pi > 0 else 0.0)
            return credit - ci - pi - cost_in - cost_out, credit, "settled"
        if stop_mult:
            T = (exp - t).total_seconds() / 3600 / (365 * 24)
            # the bar's worst point for a short straddle is its furthest excursion either way
            worst = max(rs.bs(path_hi[k], Kc, T, vols[k], True) + rs.bs(path_hi[k], Kp, T, vols[k], False),
                        rs.bs(path_lo[k], Kc, T, vols[k], True) + rs.bs(path_lo[k], Kp, T, vols[k], False))
            if worst >= stop_mult * credit:
                buy = stop_mult * credit
                cost_out = leg_fee(buy / 2, path_cl[k]) * 2 + SPREAD_RT / 2 * buy
                return credit - buy - cost_in - cost_out, credit, "stopped"
    return None


def run(df, vol, kind="straddle", width=0.0, stop_mult=None, rich_only=False):
    hi, lo, cl = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    close_t = df.index + cs.BAR
    v = vol.to_numpy()
    rv7 = (np.log(df["Close"]).diff().rolling(96 * 7).std() * math.sqrt(96 * 365)).to_numpy()
    starts = np.where((df.index.hour == SETTLE[0]) & (df.index.minute == SETTLE[1]))[0]
    rows = []
    for i in starts:
        t0 = close_t[i]
        exp = t0 + pd.Timedelta(days=1)
        j = int(np.searchsorted(close_t, exp))
        if j >= len(cl) or close_t[j] != exp or not (v[i] == v[i]):
            continue
        if rich_only and not (rv7[i] == rv7[i] and v[i] > rv7[i]):
            continue
        got = sell_day(hi[i + 1:j + 1], lo[i + 1:j + 1], cl[i + 1:j + 1], close_t[i + 1:j + 1],
                       cl[i], t0, exp, v[i], v[i + 1:j + 1], kind=kind, width=width, stop_mult=stop_mult)
        if got is None:
            continue
        pnl, credit, why = got
        rows.append({"when": t0, "pnl": pnl, "credit": credit, "why": why, "spot": cl[i]})
    return rows


def risk(rows):
    if not rows:
        return {}
    s = pd.Series([r["pnl"] for r in rows], index=pd.DatetimeIndex([r["when"] for r in rows]))
    eq = s.cumsum()
    dd = float((eq.cummax().clip(lower=0) - eq).max())
    wk = s.rolling("7D").sum()
    gain, loss = s[s > 0].sum(), -s[s < 0].sum()
    return {"n": len(s), "total": float(s.sum()), "pf": gain / loss if loss else float("inf"),
            "win": 100 * float((s > 0).mean()), "worst_day": float(s.min()), "worst_7d": float(wk.min()),
            "dd": dd, "avg_credit": float(np.mean([r["credit"] for r in rows])),
            "avg_spot": float(np.mean([r["spot"] for r in rows]))}


def main():
    print("Loading BTC history, DVOL and the measured short-dated vol ratio...")
    df, iv, _ = cs.load()
    samples = cd.short_dated_iv()
    vol = sell_vol_series(iv, samples)
    print(f"  {len(df):,} bars; {len(samples)} real short-dated vol samples, median ratio "
          f"{samples['ratio'].median():.3f}; selling vol median {vol.median():.3f} vs DVOL {iv.median():.3f}")

    V = {"straddle (hold to expiry)": run(df, vol),
         "strangle 2% (hold to expiry)": run(df, vol, kind="strangle", width=0.02),
         "straddle + stop at 2x credit": run(df, vol, stop_mult=2.0),
         "straddle, rich IV days only": run(df, vol, rich_only=True)}

    def line(name, r):
        if not r:
            return f" {name:32s} no trades"
        return (f" {name:32s} {r['n']:>4} days {r['total']:>+10,.0f} PF {r['pf']:4.2f} win {r['win']:3.0f}%"
                f"  worst day {r['worst_day']:>+8,.0f}  worst 7d {r['worst_7d']:>+8,.0f}  max DD {r['dd']:>8,.0f}"
                f"  avg credit {r['avg_credit']:>6,.0f}")

    for lbl, lo_, hi_ in (("IN-SAMPLE  Sep 2023 - Aug 2025", None, cs.SPLIT),
                          ("HELD-OUT   Aug 2025 - Sep 2026", cs.SPLIT, cs.LIVE_FROM),
                          ("LIVE WINDOW 11 Sep - 2 Oct 2026", cs.LIVE_FROM, None)):
        print(f"\n {lbl}  ($ per 1 BTC of options sold, after fees + GST + spread)")
        for name, rows in V.items():
            sub = [r for r in rows if (lo_ is None or r["when"] >= lo_) and (hi_ is None or r["when"] < hi_)]
            print(line(name, risk(sub)))

    print("\n VERDICT - profitable after costs in BOTH periods?")
    for name, rows in V.items():
        a = risk([r for r in rows if r["when"] < cs.SPLIT])
        b = risk([r for r in rows if cs.SPLIT <= r["when"] < cs.LIVE_FROM])
        ok = a and b and a["total"] > 0 and b["total"] > 0
        print(f"   {'YES' if ok else 'no '}  {name:32s} in-sample {a.get('total', 0):>+10,.0f}   held-out {b.get('total', 0):>+10,.0f}")
    return V


if __name__ == "__main__":
    main()
