#!/usr/bin/env python3
"""
gex_study.py — does the option chain's gamma decide whether a move continues or reverses? (Nifty, Bank Nifty, Sensex,
Midcap; the recorded chains since 10 Sep 2026)
================================================================================
The user, 22 Oct 2026: "think you are a senior researcher and you have to solve this problem". Published mechanism:
option dealers short gamma hedge WITH a move (it continues), long gamma AGAINST it (it reverses) - Baltussen, Da,
Lammers & Martens (JFE 2021), Barbon & Buraschi ("Gamma Fragility"). Vendors' Nifty "GEX" borrows the US sign
convention (dealers long calls, short puts) and admits NSE data cannot confirm it; no published test for India found.

THE DATA: record_options.py's minute candles - premium and open interest of the strikes around each day's range, the
nearest expiries - and the index's own minute candles. A few weeks: a first look, not proof.

FIXED BEFORE ANY RESULT WAS SEEN (22 Oct 2026). Every 15 minutes from 09:30 to 15:00, each index, the nearest expiry:
each option's implied volatility from its own premium (Black-Scholes, no rates), its gamma, and
  GEX_TOTAL  sum over strikes of (call gamma x call OI + put gamma x put OI) x spot^2 / 100 - all the gamma there is;
             if the writers (net short in India, where end users are net buyers) carry it, more of it = more hedging
             WITH the move = more continuation
  GEX_US     the US convention: calls count +, puts - (dealers long calls, short puts); per Barbon & Buraschi, negative
             = continuation, positive = reversal
Each is ranked into thirds within its own index. The move: the last 15 minutes' (the "signal") and the next 15 and 30
minutes'. Continuation = the next move goes the same way. Per third: checks, continued, average next move the last
move's way (points and basis points of the index), and how big the next 15 minutes' range was against the day's usual.
  GAMMA DECIDES only if continuation is higher in GEX_TOTAL's top third than its bottom third over 15 AND 30 minutes,
  on at least three of the four indices; for GEX_US, if its bottom third continues more than its top over both.
  Also: the tool's real Indian tickets (one live account, every system) by GEX_TOTAL third at entry.

RESULT (22 Oct 2026; gex_study_results.txt, run on the server) - GAMMA DOES NOT DECIDE IT. 2,093 checks, 27 days:
continuation over 15 / 30 minutes by GEX_TOTAL third, low 45.8% / 51.9%, middle 50.4% / 51.9%, high 49.7% / 52.1% -
high above low on both horizons on only 2 of 4 indices; GEX_US shows no order either (48-49%). Expiry days alone
continued 54.8% over 15 minutes against 47.6% (299 checks - inside the noise). The tool's 96 real tickets with a
reading: high-GEX third +337 a ticket, low +168 - all three thirds made money; too few to lean on.

    python3 gex_study.py [option_history_dir] [trades.csv]     (on the server)
"""
import glob
import math
import os
import sys

import numpy as np
import pandas as pd

HIST = os.path.expanduser("~/trading-tool-logs/option_history")
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
SQ2PI = math.sqrt(2 * math.pi)


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, T, v, call):
    if T <= 0 or v <= 0:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (math.log(S / K) + 0.5 * v * v * T) / (v * math.sqrt(T))
    d2 = d1 - v * math.sqrt(T)
    return S * _ncdf(d1) - K * _ncdf(d2) if call else K * _ncdf(-d2) - S * _ncdf(-d1)


def iv(price, S, K, T, call):
    """Implied volatility by bisection, or None when the premium is below intrinsic or above any sensible vol."""
    intrinsic = max(0.0, (S - K) if call else (K - S))
    if price is None or not (price == price) or price <= intrinsic + 0.05 or T <= 0:
        return None
    lo, hi = 0.01, 3.0
    if bs(S, K, T, hi, call) < price:
        return None
    for _ in range(50):
        mid = (lo + hi) / 2
        if bs(S, K, T, mid, call) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def gamma(S, K, T, v):
    d1 = (math.log(S / K) + 0.5 * v * v * T) / (v * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / SQ2PI / (S * v * math.sqrt(T))


def years_to(expiry, t):
    exp = pd.Timestamp(f"{expiry} 15:30")
    return max((exp - t).total_seconds() / (365 * 86400), 1e-6)


def gex_at(o, spot, t, expiry):
    """(GEX_TOTAL, GEX_US) at minute t from the last candle of each option at or before t."""
    x = o[o["ts"] <= t]
    if x.empty:
        return None, None
    last = x.sort_values("ts").groupby(["strike", "opt"])[["close", "oi"]].last()
    T = years_to(expiry, t)
    # one volatility for the chain: the median of the near-the-money implied vols (robust to stale far strikes)
    vols = []
    for (k, opt), r in last.iterrows():
        if abs(k - spot) <= spot * 0.01:
            v = iv(float(r["close"]), spot, float(k), T, opt == "CE")
            if v:
                vols.append(v)
    if not vols:
        return None, None
    vol = float(np.median(vols))
    tot = us = 0.0
    for (k, opt), r in last.iterrows():
        if abs(k - spot) > spot * 0.05 or not (r["oi"] == r["oi"]):
            continue
        g = gamma(spot, float(k), T, vol) * float(r["oi"]) * spot * spot / 100
        tot += g
        us += g if opt == "CE" else -g
    return tot, us


def checks(hist):
    rows = []
    for path in sorted(glob.glob(os.path.join(hist, "*.csv.gz"))):
        day = os.path.basename(path)[:10]
        d = pd.read_csv(path, parse_dates=["ts"])
        d["ts"] = d["ts"].dt.tz_localize(None) if d["ts"].dt.tz is not None else d["ts"]
        for key in INDICES:
            idx = d[(d["kind"] == "IDX") & (d["index"] == key)].set_index("ts")["close"].sort_index()
            o = d[(d["kind"] == "OPT") & (d["index"] == key)]
            exps = sorted(e for e in o["expiry"].dropna().unique() if e >= day)
            if idx.empty or not exps:
                continue
            o = o[o["expiry"] == exps[0]][["ts", "strike", "opt", "close", "oi"]]
            hi = d[(d["kind"] == "IDX") & (d["index"] == key)].set_index("ts")["high"].sort_index()
            lo = d[(d["kind"] == "IDX") & (d["index"] == key)].set_index("ts")["low"].sort_index()
            px = lambda tt: (lambda s: float(s.iloc[-1]) if not s.empty else None)(idx[idx.index <= tt])
            for t in pd.date_range(f"{day} 09:30", f"{day} 15:00", freq="15min"):
                s0, sb, s15 = px(t), px(t - pd.Timedelta(minutes=15)), px(t + pd.Timedelta(minutes=15))
                s30 = px(t + pd.Timedelta(minutes=30)) if t + pd.Timedelta(minutes=30) <= pd.Timestamp(f"{day} 15:30") else None
                if None in (s0, sb, s15):
                    continue
                tot, us = gex_at(o, s0, t, exps[0])
                if tot is None:
                    continue
                w = (idx.index > t) & (idx.index <= t + pd.Timedelta(minutes=15))
                rng = float(hi[w].max() - lo[w].min()) if w.any() else None
                rows.append({"day": day, "index": key, "t": t, "spot": s0, "past": s0 - sb, "f15": s15 - s0,
                             "f30": (s30 - s0) if s30 is not None else None, "rng15": rng,
                             "gex_total": tot, "gex_us": us, "expiry_day": exps[0] == day})
    return pd.DataFrame(rows)


def report(a, trades_path=None):
    W = 140
    a = a.copy()
    for col in ("gex_total", "gex_us"):
        a[col + "_3"] = a.groupby("index")[col].transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False))
    a["cont15"] = np.sign(a.f15) == np.sign(a.past)
    a["cont30"] = np.sign(a.f30) == np.sign(a.past)
    a["way15"] = np.sign(a.past) * a.f15
    a["way30"] = np.sign(a.past) * a.f30
    a["rng_x"] = a.rng15 / a.groupby(["index", "day"]).rng15.transform("mean")
    print("=" * W)
    print(f" GAMMA AND CONTINUATION - {len(a)} checks, {a.day.nunique()} days ({a.day.min()} to {a.day.max()}), four indices,"
          " the nearest expiry")
    print("=" * W)
    for col, name in (("gex_total_3", "GEX_TOTAL (all the gamma; writers short it)"), ("gex_us_3", "GEX_US (calls +, puts -)")):
        print(f" {name} - thirds within each index: checks, continued over 15 / 30 min, average next move the last move's"
              " way (pts) over 15 / 30 min, next 15 min's range against the day's")
        for q, lab in ((0, "low"), (1, "middle"), (2, "high")):
            g = a[a[col] == q]
            g30 = g[g.f30.notna()]
            print(f"   {lab:7s} {len(g):>4}   continued {g.cont15.mean():5.1%} / {g30.cont30.mean():5.1%}   "
                  f"next move {g.way15.mean():+6.1f} / {g30.way30.mean():+6.1f} pts   range {g.rng_x.mean():.2f}x")
        for key in INDICES:
            g = a[a["index"] == key]
            lo_, hi_ = g[g[col] == 0], g[g[col] == 2]
            print(f"      {key:11s} low third continued {lo_.cont15.mean():5.1%} / {lo_[lo_.f30.notna()].cont30.mean():5.1%}"
                  f"   high third {hi_.cont15.mean():5.1%} / {hi_[hi_.f30.notna()].cont30.mean():5.1%}")
    # the verdicts fixed above
    per = []
    for key in INDICES:
        g = a[a["index"] == key]
        lo_, hi_ = g[g.gex_total_3 == 0], g[g.gex_total_3 == 2]
        per.append(hi_.cont15.mean() > lo_.cont15.mean() and hi_[hi_.f30.notna()].cont30.mean() > lo_[lo_.f30.notna()].cont30.mean())
    tot_ok = sum(per) >= 3
    lo_, hi_ = a[a.gex_us_3 == 0], a[a.gex_us_3 == 2]
    us_ok = lo_.cont15.mean() > hi_.cont15.mean() and lo_[lo_.f30.notna()].cont30.mean() > hi_[hi_.f30.notna()].cont30.mean()
    print(f"\n VERDICT: GEX_TOTAL {'DECIDES' if tot_ok else 'does not decide'} continuation (high > low over 15 and 30 min on"
          f" {sum(per)} of 4 indices); GEX_US {'as the US convention says' if us_ok else 'not as the US convention says'}")
    print(f"   expiry days alone: continued {a[a.expiry_day].cont15.mean():5.1%} over 15 min ({a.expiry_day.sum()} checks),"
          f" other days {a[~a.expiry_day].cont15.mean():5.1%}")
    if trades_path:
        t = pd.read_csv(trades_path, dtype=str)
        c = t[(t["event"] == "CLOSE") & t["index"].isin(INDICES)].copy()
        o = t[t["event"] == "OPEN"].drop_duplicates("trade_id").set_index("trade_id")
        c = c[c.trade_id.isin(o.index)]
        c["pnl"] = pd.to_numeric(c.pnl, errors="coerce")
        c["lots"] = pd.to_numeric(c.lots, errors="coerce").fillna(1)
        c = c[~c.status.str.contains("manually|stopped while", case=False, na=False)]
        res = []
        for _, r in c.iterrows():
            et = pd.Timestamp(f"{r['date']} {o.loc[r['trade_id'], 'time_ist']}")
            g = a[(a["index"] == r["index"]) & (a.t <= et) & (a.day == r["date"])]
            if g.empty:
                continue
            res.append({"per_lot": r.pnl / r.lots, "third": int(g.iloc[-1].gex_total_3)})
        R = pd.DataFrame(res)
        print(f"\n THE TOOL'S REAL TICKETS (one live account, {len(R)} with a reading at entry), per lot before charges, by"
              " GEX_TOTAL third at the last check before entry")
        for q, lab in ((0, "low"), (1, "middle"), (2, "high")):
            g = R[R.third == q]
            print(f"   {lab:7s} {len(g):>4} tickets  won {(g.per_lot > 0).mean() if len(g) else 0:4.0%}  total ₹{g.per_lot.sum():>+9,.0f}"
                  f"  avg ₹{g.per_lot.mean() if len(g) else 0:>+7,.0f}")


def main(hist=HIST, trades_path=None):
    a = checks(hist)
    report(a, trades_path)
    return a


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[0] if args else HIST, args[1] if len(args) > 1 else None)
