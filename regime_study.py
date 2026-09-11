#!/usr/bin/env python3
"""
regime_study.py — does a day-type filter improve the rule set, in rupees?
================================================================================
Two things the ordinary backtest cannot tell you, answered together.

1. WHAT A TRADE IS WORTH AS AN OPTION. backtest_intraday measures index points
   in units of risk. You do not buy the index; you buy an option, and an option
   bleeds time value, carries a spread and pays STT on the way out. Each trade
   here is priced as the ATM option the tool would have suggested - Black-
   Scholes, with India VIX from the day before as the volatility (scaled for
   Bank Nifty and Sensex by their realised volatility against Nifty's) - at
   entry and again at exit, then charged Zerodha's actual costs.

2. WHETHER KNOWING THE KIND OF DAY HELPS. Candidate filters are asked at the
   moment of entry with only what was known then, and applied inside the
   backtest loop so a blocked signal frees the gap for the next one exactly as
   it would live.

WHAT GUARDS AGAINST FOOLING OURSELVES
   The last year is held out. Filters are compared on the first two years and
   the one worth keeping is only believed if it also holds on the year it was
   not chosen on. A filter that shines in-sample and fades out-of-sample is a
   description of the past, not an edge.

WHAT THIS STILL ASSUMES - read before trusting a number
   * Volatility is constant through a trade. Real IV often falls after you buy
     (IV crush) - that works against option buyers and is not modelled.
   * Days to expiry is a fixed assumption per index, not the true calendar;
     the report runs it at more than one value so you can see how much it
     matters.
   * Lot sizes are today's, applied to all three years.
   * Slippage is a percentage of premium per side. Also swept.

    python3 regime_study.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import indicators as ind

INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]
SPLIT = pd.Timestamp("2025-08-15", tz="Asia/Kolkata")     # out-of-sample from here
VIX_PATH = os.path.expanduser("~/trading-tool-logs/history/INDIAVIX_1d.csv")

# Zerodha, equity options, checked against zerodha.com/charges September 2026.
# STT went from 0.10% to 0.15% of sell-side premium on 1 April 2026.
BROKERAGE_PER_ORDER = 20.0
STT_SELL = 0.0015
TXN = {"NSE": 0.0003553, "BSE": 0.000325}
SEBI_PER_RUPEE = 10 / 1e7
STAMP_BUY = 0.00003
GST = 0.18

# Assumptions, swept in the report.
DTE_DAYS = {"NIFTY": 3.0, "SENSEX": 3.0, "BANKNIFTY": 10.0}   # BankNifty: monthly only since Nov 2024
SLIP = 0.0025                                                   # per side, share of premium


# ---------------------------------------------------------------- pricing
def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs(S, K, T, sigma, call):
    """Black-Scholes, rates and dividends ignored - both are rounding error
    over an intraday hold."""
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if call else (K - S))
    st = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / st
    d2 = d1 - st
    if call:
        return S * _ncdf(d1) - K * _ncdf(d2)
    return K * _ncdf(-d2) - S * _ncdf(-d1)


def net_rupees(p0, p1, qty, exch, slip):
    """One round trip, one lot, after every charge Zerodha lists."""
    buy = p0 * (1 + slip) * qty
    sell = max(p1 * (1 - slip), 0.0) * qty
    brokerage = 2 * BROKERAGE_PER_ORDER
    stt = STT_SELL * sell
    txn = TXN[exch] * (buy + sell)
    sebi = SEBI_PER_RUPEE * (buy + sell)
    stamp = STAMP_BUY * buy
    gst = GST * (brokerage + txn + sebi)
    return sell - buy - (brokerage + stt + txn + sebi + stamp + gst)


# ---------------------------------------------------------------- features
def load_vix():
    s = pd.read_csv(VIX_PATH, index_col=0)["vix"]
    s.index = pd.to_datetime(s.index).date
    return s


def daily_close(df):
    return df["Close"].groupby(df.index.date).last()


def features(df, vix, nifty_rv):
    """Everything a filter may look at, per bar, using only the past."""
    close, high, low = df["Close"], df["High"], df["Low"]
    f = pd.DataFrame(index=df.index)

    adx = ind.adx(df, config.ADX_LENGTH)
    atr = ind.atr(df, config.ATR_LENGTH)
    disp = (close - close.shift(config.ADX_LENGTH)).abs() / atr
    f["stalled"] = (adx >= config.ADX_TREND_THRESHOLD) & (disp < config.TREND_MIN_DISPLACEMENT_ATR)

    # Higher timeframe: yesterday's daily close against its 20-day EMA. Shifted
    # a day, so today's bars only ever see a finished day.
    dc = daily_close(df)
    ema20 = dc.ewm(span=20, adjust=False).mean()
    htf_up = (dc > ema20).shift(1)
    day = pd.Series(df.index.date, index=df.index)
    f["htf_up"] = day.map(htf_up)

    # Opening range: the first two bars, 09:15-09:45. Known from the close of
    # the 09:30 bar onward; before that there is no opening range to confirm.
    minutes = df.index.hour * 60 + df.index.minute
    first2 = minutes < (9 * 60 + 45)
    orh = high.where(first2).groupby(df.index.date).transform("max")
    orl = low.where(first2).groupby(df.index.date).transform("min")
    f["or_ready"] = minutes >= (9 * 60 + 30)
    f["above_or"] = close > orh
    f["below_or"] = close < orl

    # Entry time is the CLOSE of the bar, fifteen minutes after its stamp.
    end = minutes + 15
    f["lunch"] = (end >= 11 * 60 + 30) & (end < 13 * 60 + 30)

    # Volatility for pricing: yesterday's VIX, scaled by this index's realised
    # volatility against Nifty's so Bank Nifty is not priced as if it were Nifty.
    rets = np.log(dc).diff()
    rv = rets.rolling(20).std() * math.sqrt(252)
    ratio = (rv / nifty_rv.reindex(rv.index)).clip(0.7, 2.0).shift(1)
    vix_prev = pd.Series(vix).shift(1)
    f["sigma"] = day.map(lambda d: (vix_prev.get(d, np.nan) / 100.0)
                         * (ratio.get(d, 1.0) if not np.isnan(ratio.get(d, np.nan)) else 1.0))
    f["sigma"] = f["sigma"].ffill()
    return f


# ---------------------------------------------------------------- filters
def make_gates(F):
    """name -> gate(i, rec). Each answers "may this signal be taken?"."""
    st, up, ready = F["stalled"].to_numpy(), F["htf_up"].to_numpy(), F["or_ready"].to_numpy()
    aor, bor, lunch = F["above_or"].to_numpy(), F["below_or"].to_numpy(), F["lunch"].to_numpy()

    def with_htf(i, rec):
        u = up[i]
        if u is None or (isinstance(u, float) and np.isnan(u)):
            return False
        return bool(u) if rec["option_type"] == "CE" else not bool(u)

    def orb(i, rec):
        if not ready[i]:
            return False
        return bool(aor[i]) if rec["option_type"] == "CE" else bool(bor[i])

    g = {
        "baseline":            None,
        "no stalled trend":    lambda i, r: not st[i],
        "with daily trend":    with_htf,
        "opening-range break": orb,
        "skip 11:30-13:30":    lambda i, r: not lunch[i],
    }
    g["daily trend + OR break"] = lambda i, r: with_htf(i, r) and orb(i, r)
    g["OR break + skip lunch"] = lambda i, r: orb(i, r) and not lunch[i]
    g["all four"] = lambda i, r: (not st[i]) and with_htf(i, r) and orb(i, r) and not lunch[i]
    return g


# ---------------------------------------------------------------- scoring
def price_trades(key, trades, F, dte, slip):
    meta = config.INSTRUMENTS[key]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    sig = F["sigma"]
    out = []
    for t in trades:
        s = sig.get(t["when"], np.nan)
        if s is None or np.isnan(s) or s <= 0:
            continue
        call = t["side"] == "CE"
        K = round(t["entry"] / step) * step
        T0 = dte / 365.0
        T1 = max(T0 - t["bars_held"] * 15 / (60 * 24 * 365.0), 1e-6)
        p0 = bs(t["entry"], K, T0, s, call)
        p1 = bs(t["exit"], K, T1, s, call)
        if p0 <= 0.5:
            continue
        out.append({"when": t["when"], "net": net_rupees(p0, p1, qty, exch, slip),
                    "gross": (p1 - p0) * qty, "p0": p0})
    return out


def stats(rows):
    if not rows:
        return None
    net = np.array([r["net"] for r in rows])
    gross = np.array([r["gross"] for r in rows])
    eq = np.cumsum(net)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    return {"n": len(net), "win": float((net > 0).mean() * 100),
            "avg": float(net.mean()), "total": float(net.sum()),
            "gross_avg": float(gross.mean()),
            "pf": float(wins / losses) if losses else float("inf"), "dd": dd}


def run_all(dte_map, slip, hists, feats, quiet=False):
    results = {}
    for key in INDICES:
        gates = make_gates(feats[key])
        for name, gate in gates.items():
            res = bt.run(key, hists[key], gate=gate)
            rows = price_trades(key, res["trades"], feats[key], dte_map[key], slip)
            ins = [r for r in rows if r["when"] < SPLIT]
            oos = [r for r in rows if r["when"] >= SPLIT]
            results[(key, name)] = {"all": stats(rows), "is": stats(ins), "oos": stats(oos)}
    return results


def fmt(s):
    if not s:
        return "      —"
    return (f"{s['n']:>5} trades  win {s['win']:4.1f}%  avg ₹{s['avg']:>+8.0f}/lot  "
            f"PF {s['pf']:4.2f}")


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = load_vix()
    nrets = np.log(daily_close(hists["NIFTY"])).diff()
    nifty_rv = nrets.rolling(20).std() * math.sqrt(252)
    feats = {k: features(hists[k], vix, nifty_rv) for k in INDICES}

    print(f"\nPricing as ATM options, days-to-expiry {DTE_DAYS}, slippage "
          f"{SLIP*100:.2f}% a side, Zerodha charges incl. STT 0.15%.")
    print(f"In-sample: to {SPLIT.date()}   Out-of-sample: {SPLIT.date()} onward\n")
    R = run_all(DTE_DAYS, SLIP, hists, feats)

    names = list(make_gates(feats["NIFTY"]).keys())
    for key in INDICES:
        print("=" * 104)
        print(f" {key}")
        print("=" * 104)
        print(f" {'filter':24s} {'IN-SAMPLE (2 yrs)':44s} {'OUT-OF-SAMPLE (last yr)':44s}")
        for name in names:
            r = R[(key, name)]
            print(f" {name:24s} {fmt(r['is']):44s}   {fmt(r['oos'])}")
        print()

    # Pooled, so one index's luck does not decide the verdict.
    print("=" * 104)
    print(" ALL THREE POOLED — total ₹ per lot over the period, and worst drawdown")
    print("=" * 104)
    for name in names:
        tot_is = sum((R[(k, name)]["is"] or {}).get("total", 0) for k in INDICES)
        tot_oos = sum((R[(k, name)]["oos"] or {}).get("total", 0) for k in INDICES)
        n_is = sum((R[(k, name)]["is"] or {}).get("n", 0) for k in INDICES)
        n_oos = sum((R[(k, name)]["oos"] or {}).get("n", 0) for k in INDICES)
        print(f" {name:24s} in-sample {n_is:>5} trades ₹{tot_is:>+11,.0f}   "
              f"out-of-sample {n_oos:>5} trades ₹{tot_oos:>+11,.0f}")

    # How much do the assumptions matter? Baseline and the best pooled
    # out-of-sample filter, at other expiry and slippage settings.
    print("\n" + "=" * 104)
    print(" SENSITIVITY — pooled out-of-sample ₹, same trades, different assumptions")
    print("=" * 104)
    for label, dte, slip in (("expiry day (DTE 1)", {k: 1.0 for k in INDICES}, SLIP),
                             ("default", DTE_DAYS, SLIP),
                             ("far expiry (DTE 6/15)", {"NIFTY": 6.0, "SENSEX": 6.0, "BANKNIFTY": 15.0}, SLIP),
                             ("no slippage", DTE_DAYS, 0.0),
                             ("double slippage", DTE_DAYS, SLIP * 2)):
        Rs = run_all(dte, slip, hists, feats, quiet=True)
        cells = []
        for name in ("baseline", "opening-range break", "OR break + skip lunch", "skip 11:30-13:30"):
            tot = sum((Rs[(k, name)]["oos"] or {}).get("total", 0) for k in INDICES)
            cells.append(f"{name}: ₹{tot:>+10,.0f}")
        print(f" {label:22s} " + "   ".join(cells))


if __name__ == "__main__":
    main()
