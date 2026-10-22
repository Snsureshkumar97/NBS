#!/usr/bin/env python3
"""
reversal_study.py — what did the entries that reversed into a loss have in common, and can it be avoided?
================================================================================
The user, 22 Oct 2026: "i want to avoid the trades where the market is getting reversal research about the reveral and
how to avoid trades where we loose because of reversal of the market". From the research: the "noise area" of
Zarattini, Aziz & Barbon (Beat the Market, 2024) - the day's open +/- the average absolute move from the open at that
time of day over the last 14 days; inside it, no real imbalance, so a trend entry there is more likely to reverse;
large opening moves partly reverting (Grant, Wolf & Yu 2005); continuation or reversal depending on conditions
(Yu, Rentzler & Wolf 2005); choppiness / efficiency-ratio filters (practitioners, untested).

FIXED BEFORE ANY RESULT WAS SEEN (22 Oct 2026). Every trade, each index on the system it runs: the rules on Nifty,
Sensex and Midcap (order_block_study.rules_rows) and the TradingView rating on Nifty and Bank Nifty (tv_ratings_study
tv_ours). At its entry (the last 15-minute close known then), in the trade's direction:
  noise      the move from the day's open over the 14-day average move from the open at that time (>1: outside the
             noise area the trade's way)            from_open  the move from the day's open, in ATRs
  vwap       distance from VWAP, in ATRs            run3       the last three closes' move, in ATRs
  body       the signal candle's body, in ATRs      rsi        RSI (the trade's way: a put's is 100 - RSI)
  adx        ADX                                    adx_slope  ADX now minus three closes ago
  er10       efficiency ratio over 10 closes (1 = a straight line)
  chop14     choppiness index over 14 closes (high = choppy)
  used       the day's range so far over the usual day's range          gap  the opening gap, %, the trade's way
  tod        minutes since 09:15                    vix  India VIX, the day before        trade_no  the day's Nth entry
  1. DISCOVER, FIRST TWO YEARS ONLY: each feature cut into fifths (cut points from these years); per fifth the trades,
     the share lost, the average per lot after costs - for the rules and the rating separately. A candidate is an
     outer fifth (lowest or highest) that LOST money on average for BOTH systems, with at least 5% of trades.
  2. CONFIRM ON THE HELD-OUT YEAR (cut points unchanged): a candidate is confirmed only if that fifth lost money there
     too for both systems.
  3. TEST: every confirmed candidate, and the research's own noise-area rule (enter only outside the noise area the
     trade's way, noise > 1), run as an entry filter inside the backtest (a held signal frees the slot, as live),
     against today, both periods, pooled and per index, with robustness.py's lines.

RESULT (22 Oct 2026, history to 11 Sep 2026; reversal_study_results.txt) - NO SIGN AT ENTRY OF A REVERSAL; THE NOISE
AREA CUTS LOSSES AND DRAWDOWN BUT COSTS PROFIT. NOTHING BUILT.
  1. 3,354 first-two-year trades: no feature has an outer fifth that lost money on average for both systems - no
     candidate, so nothing to confirm. The losers look like the winners at entry. If anything the reverse of "avoid
     extended entries": the trades already moving hardest the trade's way did BEST - outside the noise area (top
     fifth) +555 a trade on the rules and +797 on the rating, against +130 / +100 or so for the rest; the same for
     the move from the open, RSI, the efficiency ratio and the day's range used. The weakest were the middle ones.
  3. The research's noise-area rule (enter only outside the noise area the trade's way) skips about half the trades:
     the rules win 54% instead of 51%, the worst drop falls from 84,323 to 30,928 in the held-out year and the worst
     day from -29,422 to -10,730 - but profit falls in both periods: rules pooled -75,625 / -69,647 (2 of 7 half-years),
     the rating on Nifty -41,283 / -43,720, on Bank Nifty -39,401 / -57,259. DROP as a profit rule; a way to trade
     less and lose less, at about a third less profit, if that is ever wanted.

    /usr/bin/python3 reversal_study.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("NIFTY", "SENSEX", "MIDCPNIFTY")
TV = ("NIFTY", "BANKNIFTY")
FEATURES = ("noise", "from_open", "vwap", "run3", "body", "rsi", "adx", "adx_slope", "er10", "chop14", "used", "gap",
            "tod", "vix", "trade_no")
NOISE_DAYS = 14


def feature_table(key):
    """{name: array over the 15-minute bars} of the direction-free parts, and the day's open per bar."""
    import backtest_intraday as bt
    import regime_study as rs
    df = bt.fetch_history(key, years=3, use_cache=True)
    pre = bt.precompute(df, key)
    o, h, l, c = (df[x].to_numpy(float) for x in ("Open", "High", "Low", "Close"))
    day = pd.Series(df.index.date, index=df.index)
    dopen = df["Open"].groupby(day.values).transform("first").to_numpy(float)
    slot = pd.Series(df.index.time, index=df.index)
    m = pd.DataFrame({"day": day.values, "slot": slot.values, "m": np.abs(c / dopen - 1)})
    piv = m.pivot_table(index="day", columns="slot", values="m")
    sig = piv.rolling(NOISE_DAYS, min_periods=10).mean().shift(1)
    sigma = np.array([sig.at[d, s] if (d in sig.index and s in sig.columns) else np.nan for d, s in zip(m["day"], m["slot"])])
    atr = pre["atr"].to_numpy(float)
    prev_close = pd.Series(c, index=df.index).groupby(day.values).transform("last").shift(1)
    first = ~day.duplicated().to_numpy()
    pc = pd.Series(np.where(first, prev_close.to_numpy(), np.nan)).ffill().to_numpy()
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[np.nan, c[:-1]]), np.abs(l - np.r_[np.nan, c[:-1]])))
    tr_sum = pd.Series(tr).rolling(14).sum().to_numpy()
    rng = pd.Series(h).rolling(14).max().to_numpy() - pd.Series(l).rolling(14).min().to_numpy()
    chop = 100 * np.log10(tr_sum / rng) / math.log10(14)
    absd = pd.Series(np.abs(np.diff(c, prepend=np.nan))).rolling(10).sum().to_numpy()
    er = np.abs(c - np.r_[np.full(10, np.nan), c[:-10]]) / absd
    adx = pre["adx"].to_numpy(float)
    vix = rs.load_vix()
    vix_prev = vix.shift(1)
    vix_by_day = {d: float(v) for d, v in zip(vix_prev.index.date if hasattr(vix_prev.index, "date") else vix_prev.index, vix_prev.values)}
    tod = np.array([(t.hour * 60 + t.minute + 15) - (9 * 60 + 15) for t in df.index])
    return {"df": df, "c": c, "o": o, "dopen": dopen, "sigma": sigma, "atr": atr, "vwap": pre["vwap"].to_numpy(float),
            "rsi": pre["rsi"].to_numpy(float), "adx": adx, "adx_slope": adx - np.r_[np.full(3, np.nan), adx[:-3]],
            "er10": er, "chop14": chop, "used": (pre["used_today"] / pre["typical"]).to_numpy(float),
            "gap": (dopen / pc - 1) * 100, "tod": tod, "days": day.values, "vix_by_day": vix_by_day}


def feats(T, i, buy):
    s = 1 if buy else -1
    a = T["atr"][i]
    c = T["c"][i]
    return {"noise": (c / T["dopen"][i] - 1) * s / T["sigma"][i] if T["sigma"][i] else np.nan,
            "from_open": (c - T["dopen"][i]) * s / a, "vwap": (c - T["vwap"][i]) * s / a,
            "run3": (c - T["c"][i - 3]) * s / a if i >= 3 else np.nan, "body": (c - T["o"][i]) * s / a,
            "rsi": T["rsi"][i] if buy else 100 - T["rsi"][i], "adx": T["adx"][i], "adx_slope": T["adx_slope"][i],
            "er10": T["er10"][i], "chop14": T["chop14"][i], "used": T["used"][i], "gap": T["gap"][i] * s,
            "tod": T["tod"][i], "vix": T["vix_by_day"].get(T["days"][i], np.nan)}


def bar_of(T, when):
    """The last 15-minute bar closed at or before `when`."""
    idx = T["df"].index
    return int(idx.searchsorted(pd.Timestamp(when) - pd.Timedelta(minutes=15), side="right")) - 1


# the trades, with or without a filter
def _rules_job(job):
    key, flt = job
    import order_block_study as ob

    def make(df, pre):
        if flt is None:
            return None, None, None
        T = feature_table(key)
        return (lambda i, buy: keep(flt, feats(T, i, buy))), None, None
    return job, ob.rules_rows(key, make)


def _tv_job(job):
    key, flt = job
    import tv_ratings_study as tvs
    if flt is not None:
        real = tvs.tv_ratings
        T = feature_table(key)

        def filtered(df, real=real):
            rt = real(df).copy()
            r = rt["all"].to_numpy(float).copy()
            for i in range(len(r)):
                if (r[i] > tvs.STRONG or r[i] < -tvs.STRONG) and not keep(flt, feats(T, i, r[i] > 0)):
                    r[i] = 0.0
            rt["all"] = r
            return rt
        tvs.tv_ratings = filtered
    _, out = tvs._tv_job(key)
    return job, out["tv_ours"]


def keep(flt, f):
    """A filter (name, side, cut): side "lo" skips f[name] <= cut, "hi" skips f[name] >= cut. Missing values pass."""
    name, side, cut = flt
    v = f.get(name)
    if v is None or not (v == v):
        return True
    return v > cut if side == "lo" else v < cut


def collect(pool, flt=None):
    jobs = [("rules", (k, flt)) for k in RULES] + [("tv", (k, flt)) for k in TV]
    r = dict(pool.map(_rules_job, [j for s, j in jobs if s == "rules"], chunksize=1))
    t = dict(pool.map(_tv_job, [j for s, j in jobs if s == "tv"], chunksize=1))
    return {("rules", k): r[(k, flt)] for k in RULES} | {("tv", k): t[(k, flt)] for k in TV}


def annotate(got):
    import stop_day_study as sds
    out = []
    for (system, key), rows in got.items():
        T = feature_table(key)
        per_day = {}
        for r in sorted(rows, key=lambda x: x["when"]):
            i = bar_of(T, r["when"])
            if i < 20:
                continue
            d = T["days"][i]
            per_day[d] = per_day.get(d, 0) + 1
            f = feats(T, i, r["side"] == "CE")
            f["trade_no"] = per_day[d]
            out.append({"system": system, "index": key, "net": r["net"], "is": r["when"] < sds.SPLIT,
                        "full_stop": r.get("closed_via") == "stop", **f})
    return pd.DataFrame(out)


def main():
    import multiprocessing as mp
    import order_block_study as ob
    import stop_day_study as sds
    W = 160
    with mp.get_context("spawn").Pool(processes=5, maxtasksperchild=1) as pool:
        base = collect(pool)
        for k, figs in ob.B_FIGS.items():
            s = sds.split(base[("rules", k)])
            assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} rules must be room_fix_study's B {figs}"
        s = sds.split(base[("tv", "BANKNIFTY")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == ob.TV_FIGS, "Bank Nifty must be tv_ours"
        A = annotate(base)
        ins, oos = A[A["is"]], A[~A["is"]]
        print("=" * W)
        print(f" 1. DISCOVER - FIRST TWO YEARS ONLY: {len(ins)} trades ({(ins.system == 'rules').sum()} rules, "
              f"{(ins.system == 'tv').sum()} TradingView rating). Per fifth of each feature: trades, lost, full stop, average"
              f" per lot after costs, ₹")
        print("=" * W)
        cands = []
        for name in FEATURES:
            x = ins[name].dropna()
            if x.nunique() < 5:
                continue
            cuts = list(np.quantile(x, [0.2, 0.4, 0.6, 0.8]))
            band = lambda v: (np.searchsorted(cuts, v, side="right") if v == v else -1)
            print(f" {name}   cut points {', '.join(f'{c:.2f}' for c in cuts)}")
            for system in ("rules", "tv"):
                g = ins[ins.system == system]
                b = g[name].apply(band)
                cells = []
                for q in range(5):
                    gg = g[b == q]
                    cells.append(f"Q{q + 1} {len(gg):>4} lost {(gg.net <= 0).mean() if len(gg) else 0:4.0%} "
                                 f"₹{gg.net.mean() if len(gg) else 0:>+5,.0f}")
                print(f"   {system:5s} " + "  |  ".join(cells))
            for q, side, cut in ((0, "lo", cuts[0]), (4, "hi", cuts[3])):
                ok = True
                for system in ("rules", "tv"):
                    g = ins[ins.system == system]
                    gg = g[g[name].apply(band) == q]
                    if len(gg) < 0.05 * len(g) or gg.net.mean() >= 0:
                        ok = False
                if ok:
                    cands.append((name, side, float(cut)))
        print(f"\n CANDIDATES (an outer fifth that lost money on average for both systems): "
              + (", ".join(f"{n} {'<=' if s == 'lo' else '>='} {c:.2f}" for n, s, c in cands) or "none"))
        print("\n" + "=" * W)
        print(" 2. CONFIRM ON THE HELD-OUT YEAR (cut points from the first two years)")
        print("=" * W)
        confirmed = []
        for name, side, cut in cands:
            sel = (oos[name] <= cut) if side == "lo" else (oos[name] >= cut)
            parts = []
            ok = True
            for system in ("rules", "tv"):
                gg = oos[(oos.system == system) & sel]
                parts.append(f"{system} {len(gg)} trades avg ₹{gg.net.mean() if len(gg) else 0:+,.0f}")
                if not len(gg) or gg.net.mean() >= 0:
                    ok = False
            print(f"   {name} {'<=' if side == 'lo' else '>='} {cut:.2f}: " + ";  ".join(parts) + ("  CONFIRMED" if ok else "  not confirmed"))
            if ok:
                confirmed.append((name, side, cut))
        tests = [("noise", "lo", 1.0)] + [c for c in confirmed if c[0] != "noise" or c[1] != "lo"]
        print("\n" + "=" * W)
        print(" 3. TEST - each as an entry filter inside the backtest, against today, per lot after costs")
        print("=" * W)
        for flt in tests:
            got = collect(pool, flt)
            name = f"skip {flt[0]} {'<=' if flt[1] == 'lo' else '>='} {flt[2]:.2f}" + ("  (the noise-area rule)" if flt == ("noise", "lo", 1.0) else "")
            rules_new = sorted([r for k in RULES for r in got[("rules", k)]], key=lambda r: r["when"])
            rules_old = sorted([r for k in RULES for r in base[("rules", k)]], key=lambda r: r["when"])
            print(f" {name}")
            print("   rules, Nifty + Sensex + Midcap:")
            print("     " + sds.line("today", rules_old).strip())
            print("     " + sds.line("filtered", rules_new).strip())
            print("     " + sds.verdict(f"{name} - rules pooled", rules_new, rules_old).strip().replace("\n", "\n     "))
            for k in RULES:
                print("        " + sds.verdict(f"rules {k}", got[("rules", k)], base[("rules", k)]).strip().split("\n")[0])
            for k in TV:
                print("     " + sds.verdict(f"TradingView rating {k}", got[("tv", k)], base[("tv", k)]).strip().replace("\n", "\n     "))
    return A


if __name__ == "__main__":
    main()
