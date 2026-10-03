#!/usr/bin/env python3
"""
cfd_vote_search.py — mix and match the votes of every strategy found, for Exness BTC and gold
================================================================================
The user, 4 Oct 2026: "check all the startegies in the world and check what votes makes
profitable improve win rate and change that in this signal card ... good win rate and low loss
mix and match all startegies you find different combination and bring out the best one".

THE VOTES (each +1 buy / -1 sell / 0 none, from closed bars only - no look-ahead):
  the tool's own: ema_trend (EMA20 vs 50), macd (histogram), rsi50, vwap (daily, tick volume),
  supertrend (10, 2.5), di (+DI vs -DI), engine (the live engine's whole call at its ADX gate);
  from the published strategies ported in exness_strategy_shootout.py: bb_revert (Bollinger 20,2
  - BbandRsi / Flawless Victory buy the lower band), rsi2 (Connors: RSI2 < 10 above SMA200 /
  > 90 below), donchian (Turtle: close beyond the prior 20-bar high / low), ema5_10 (hlhb),
  ha (Heikin-Ashi colour - Strategy001), asian_break (the London breakout), pdhl (beyond the
  previous day's high / low - the sweep strategy's levels); and common ones: ema200 (price vs
  EMA200), ema50_200, h1_trend (EMA80 vs 200 on 15m = the 1-hour 20/50), d1_trend (vs the
  previous day's close), mfi50, stoch50, candle (this bar's colour), roc12 (momentum).
THE FILTERS (yes / no): adx20, adx25, adx30, adx_rising, session (07:00-20:00 UTC, London+NY),
  vol_rising (tick volume 5 vs 20 bars), tight_spread (spread under 1.5x its 30-day median).

AN ENTRY: every chosen vote says the same side, and every chosen filter is true. THE EXIT: a stop
at m x ATR(14) and a target at t x that distance, 24 hours at most - every outcome read off
cfd_outcomes.py's tick-by-tick table (Exness's own ticks, its spread in and out), plus Exness's
overnight swap on buys (cfd_tick_study.SWAP). One position at a time, 20 minutes after an exit.

THE SEARCH, on the IN-SAMPLE years only (to 15 Aug 2025): a beam search - every single vote,
then the BEAM best grow by one vote or filter at a time, up to MAX_PARTS - each candidate
scored over all 28 stop/target pairs. Two objectives, reported side by side:
  "profit"    the most net profit, at least MIN_TRADES trades
  "accuracy"  the most net profit among rules that WIN at least MIN_WIN of their trades
THEN, once, the held-out year (15 Aug 2025 - 31 Aug 2026), which the search never saw. A rule
is only worth anything if it holds up there. Thousands of combinations are tried; some look
good on the past by pure chance - the held-out year is what tells them apart.

    python3 cfd_vote_search.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

BEAM = 6
MAX_PARTS = 5
MIN_TRADES = 300        # in-sample, ~2 years
MIN_WIN = 0.55
COOLDOWN_NS = 20 * 60 * 10 ** 9
SPLIT = pd.Timestamp("2025-08-15", tz="Asia/Kolkata").value


# ---------------------------------------------------------------- indicators
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rma(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def rsi(s, n):
    d = s.diff()
    up, dn = rma(d.clip(lower=0), n), rma((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def votes_and_filters(full, key, engine_bias):
    """{name: int8 array} votes and {name: bool array} filters, one per 15-minute bar."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import config
    import indicators as ind
    o, h, l, c, v = (full[k] for k in ("Open", "High", "Low", "Close", "Volume"))
    t = full.index.tz_convert("UTC")
    day = t.normalize()
    sgn = lambda x: np.sign(np.nan_to_num(np.asarray(x, dtype=float))).astype(np.int8)
    V = {}
    V["ema_trend"] = sgn(ema(c, 20) - ema(c, 50))
    macd = ema(c, 12) - ema(c, 26)
    V["macd"] = sgn(macd - ema(macd, 9))
    r14 = rsi(c, 14)
    V["rsi50"] = sgn(r14 - 50)
    tp = (h + l + c) / 3
    vw = (tp * v).groupby(day).cumsum() / v.groupby(day).cumsum().replace(0, np.nan)
    V["vwap"] = sgn(c - vw)
    st = ind.supertrend(full[["Open", "High", "Low", "Close", "Volume"]], *config.supertrend_params(key))[0]
    V["supertrend"] = sgn(c - st)
    up_, dn_ = h.diff(), -l.diff()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = rma(tr, 14)
    pdi = 100 * rma(up_.where((up_ > dn_) & (up_ > 0), 0.0), 14) / atr
    ndi = 100 * rma(dn_.where((dn_ > up_) & (dn_ > 0), 0.0), 14) / atr
    adx = rma(100 * (pdi - ndi).abs() / (pdi + ndi), 14)
    V["di"] = sgn(pdi - ndi)
    V["engine"] = np.array([1 if b == "CE" else -1 if b == "PE" else 0 for b in engine_bias], dtype=np.int8)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    V["bb_revert"] = np.where(c < mid - 2 * sd, 1, np.where(c > mid + 2 * sd, -1, 0)).astype(np.int8)
    r2, s200 = rsi(c, 2), c.rolling(200).mean()
    V["rsi2"] = np.where((r2 < 10) & (c > s200), 1, np.where((r2 > 90) & (c < s200), -1, 0)).astype(np.int8)
    hh, ll = h.rolling(20).max().shift(), l.rolling(20).min().shift()
    V["donchian"] = np.where(c > hh, 1, np.where(c < ll, -1, 0)).astype(np.int8)
    V["ema5_10"] = sgn(ema(c, 5) - ema(c, 10))
    ha_c = (o + h + l + c) / 4
    ha_o = np.empty(len(full)); ha_o[0] = (o.iloc[0] + c.iloc[0]) / 2
    hc = ha_c.to_numpy()
    for i in range(1, len(full)):
        ha_o[i] = (ha_o[i - 1] + hc[i - 1]) / 2
    V["ha"] = sgn(hc - ha_o)
    mins = (t.hour * 60 + t.minute).to_numpy()
    asian = mins < 7 * 60
    ah = h.where(asian).groupby(day).cummax().groupby(day).ffill()
    al = l.where(asian).groupby(day).cummin().groupby(day).ffill()
    after = mins >= 7 * 60
    V["asian_break"] = np.where(after & (c > ah), 1, np.where(after & (c < al), -1, 0)).astype(np.int8)
    dly = pd.DataFrame({"h": h.groupby(day).max(), "l": l.groupby(day).min(), "c": c.groupby(day).last()}).shift()
    pdh, pdl, pdc = (dly[k].reindex(day).to_numpy() for k in ("h", "l", "c"))
    V["pdhl"] = np.where(c > pdh, 1, np.where(c < pdl, -1, 0)).astype(np.int8)
    V["d1_trend"] = sgn(c.to_numpy() - pdc)
    V["ema200"] = sgn(c - ema(c, 200))
    V["ema50_200"] = sgn(ema(c, 50) - ema(c, 200))
    V["h1_trend"] = sgn(ema(c, 80) - ema(c, 200))
    tp_ch = tp.diff()
    mf_up = (v * tp.where(tp_ch > 0, 0.0)).rolling(14).sum()
    mf_dn = (v * tp.where(tp_ch < 0, 0.0)).rolling(14).sum()
    V["mfi50"] = sgn(100 - 100 / (1 + mf_up / mf_dn.replace(0, np.nan)) - 50)
    k_ = 100 * (c - l.rolling(14).min()) / (h.rolling(14).max() - l.rolling(14).min()).replace(0, np.nan)
    V["stoch50"] = sgn(k_.rolling(3).mean() - 50)
    V["candle"] = sgn(c - o)
    V["roc12"] = sgn(c - c.shift(12))
    F = {"adx20": (adx >= 20).to_numpy(), "adx25": (adx >= 25).to_numpy(), "adx30": (adx >= 30).to_numpy(),
         "adx_rising": (adx > adx.shift(3)).to_numpy(),
         "session": ((mins >= 7 * 60) & (mins < 20 * 60)),
         "vol_rising": (v.rolling(5).mean() > v.rolling(20).mean()).to_numpy(),
         "tight_spread": (full["spread_close"] <= 1.5 * full["spread_close"].rolling(96 * 30, min_periods=96).median()).to_numpy()}
    return V, F


# ---------------------------------------------------------------- swap and the one-position simulator
def rollover_weights(start_ns, end_ns, triple_wd):
    """Cumulative weighted 21:00 UTC rollovers (Mon-Fri, x3 on the triple day) at each day's 21:00."""
    d0 = pd.Timestamp(start_ns, tz="UTC").normalize() - pd.Timedelta(days=2)
    days = pd.date_range(d0, pd.Timestamp(end_ns, tz="UTC").normalize() + pd.Timedelta(days=3), freq="D")
    rolls = (days + pd.Timedelta(hours=21)).asi8
    w = np.array([0 if d.weekday() >= 5 else (3 if d.weekday() == triple_wd else 1) for d in days])
    return rolls, np.cumsum(w)


class Sim:
    def __init__(self, net, ext, ok, entry_ns, side_long_swap, rolls, cumw, mult):
        self.net, self.ext, self.ok, self.entry = net, ext, ok, entry_ns
        self.swap, self.rolls, self.cumw, self.mult = side_long_swap, rolls, cumw, mult

    def _swap_nights(self, t0, t1):
        a = np.searchsorted(self.rolls, t0, side="right") - 1
        b = np.searchsorted(self.rolls, t1, side="right") - 1
        return self.cumw[b] - self.cumw[a]

    def run(self, cand_idx, cand_side, k, j):
        """One position at a time; returns (when array, $ per lot array) for the taken trades.
        Every candidate's next possible entry (its exit + the cooldown) is found at once with
        searchsorted; then the chain from the first candidate is followed - a loop over TRADES,
        all the arithmetic in arrays."""
        if len(cand_idx) == 0:
            return np.array([], dtype=np.int64), np.array([])
        sd = (cand_side < 0).astype(np.int64)
        x = self.net[cand_idx, sd, k, j]
        valid = ~np.isnan(x)
        ci, si, sdv, x = cand_idx[valid], cand_side[valid], sd[valid], x[valid].astype(float)
        if len(ci) == 0:
            return np.array([], dtype=np.int64), np.array([])
        t0 = self.entry[ci]
        t1 = self.ext[ci, sdv, k, j]
        nxt = np.searchsorted(t0, t1 + COOLDOWN_NS, side="left").tolist()
        taken, p, n = [], 0, len(ci)
        while p < n:
            taken.append(p)
            p = max(nxt[p], p + 1)          # never stand still, whatever the table holds
        tk = np.array(taken, dtype=np.int64)
        val = x[tk]
        if self.swap:
            a = np.searchsorted(self.rolls, t0[tk], side="right") - 1
            b = np.searchsorted(self.rolls, t1[tk], side="right") - 1
            nights = self.cumw[b] - self.cumw[a]
            val = val + np.where(si[tk] > 0, nights * self.swap / self.mult, 0.0)
        return t0[tk], val * self.mult


def stats(nets):
    if len(nets) == 0:
        return {"n": 0, "net": 0.0, "win": 0.0, "pf": 0.0, "dd": 0.0}
    eq = np.cumsum(nets)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq))
    g, lo = nets[nets > 0].sum(), -nets[nets < 0].sum()
    return {"n": int(len(nets)), "net": float(nets.sum()), "win": float((nets > 0).mean()),
            "pf": float(g / lo) if lo else float("inf"), "dd": max(dd, 0.0)}


# ---------------------------------------------------------------- the search
def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pickle
    import cfd_tick_study as cts
    import exness_data as ed
    report = {}
    for sym, (key, mult, gate) in {"BTCUSD": ("BTC", 1.0, 20), "XAUUSD": ("GOLD", 100.0, 25)}.items():
        full = ed.load(sym)
        O = np.load(os.path.join(ed._dir(), f"outcomes_{sym}.npz"))
        net, ext, ok = O["net"], O["ext"], O["ok"]
        stops, targets = list(O["stops"]), list(O["targets"])
        with open(os.path.join(ed._dir(), f"adx_{sym}_{gate}_{len(full)}.pkl"), "rb") as fh:
            engine_bias = pickle.load(fh)["opp"]
        V, F = votes_and_filters(full, key, engine_bias)
        entry_ns = full.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
        long_sw, _, triple = cts.SWAP[sym]
        rolls, cumw = rollover_weights(entry_ns[0], entry_ns[-1], triple)
        sim = Sim(net, ext, ok, entry_ns, long_sw, rolls, cumw, mult)
        ins = entry_ns < SPLIT
        oos = ~ins
        names_v, names_f = sorted(V), sorted(F)

        def signal(parts):
            vs = [V[p] for p in parts if p in V]
            fs = [F[p] for p in parts if p in F]
            if not vs:
                return None
            agree = np.all(np.stack([x == vs[0] for x in vs]), axis=0) & (vs[0] != 0) & ok
            for f in fs:
                agree &= f
            return agree, vs[0]

        def score(parts, mask):
            sg = signal(parts)
            if sg is None:
                return []
            agree, side = sg
            idx = np.flatnonzero(agree & mask)
            out = []
            for k in range(len(stops)):
                for j in range(len(targets)):
                    w, n_ = sim.run(idx, side[idx], k, j)
                    out.append((k, j, stats(n_)))
            return out

        def best_of(parts, objective):
            best = None
            for k, j, s in score(parts, ins):
                if s["n"] < MIN_TRADES or (objective == "accuracy" and s["win"] < MIN_WIN):
                    continue
                if best is None or s["net"] > best[2]["net"]:
                    best = (k, j, s)
            return best

        print(f"\n{'=' * 120}\n {sym}: {len(names_v)} votes, {len(names_f)} filters, {len(stops)} x {len(targets)} exits; "
              f"in-sample to 15 Aug 2025\n{'=' * 120}", flush=True)
        report[sym] = {}
        for objective in ("profit", "accuracy"):
            seen, tried = {}, 0
            frontier = [(p,) for p in names_v]
            for depth in range(1, MAX_PARTS + 1):
                scored = []
                for parts in frontier:
                    keyp = tuple(sorted(parts))
                    if keyp in seen:
                        continue
                    b = best_of(keyp, objective)
                    tried += 1
                    seen[keyp] = b
                    if b:
                        scored.append((b[2]["net"], keyp, b))
                scored.sort(key=lambda x: -x[0])
                top = scored[:BEAM]
                if not top:
                    break
                print(f"  [{objective}] depth {depth}: best in-sample {top[0][0]:+,.0f} with {top[0][1]} "
                      f"(stop {stops[top[0][2][0]]:g} ATR, target {targets[top[0][2][1]]:g}R, "
                      f"win {100 * top[0][2][2]['win']:.0f}%, {top[0][2][2]['n']} trades)", flush=True)
                frontier = [tuple(sorted(set(p) | {x})) for _, p, _ in top for x in names_v + names_f if x not in p]
            ranked = sorted(((b[2]["net"], p, b) for p, b in seen.items() if b), key=lambda x: -x[0])[:5]
            report[sym][objective] = []
            print(f"\n  [{objective}] {tried} rules tried. The 5 best IN-SAMPLE, then the held-out year they never saw:")
            print(f"  {'rule':58s} {'exit':14s} {'IN-SAMPLE n win% net PF':>32s}   {'HELD-OUT n win% net PF maxDD':>40s}")
            for _, p, (k, j, s) in ranked:
                sg = signal(p)
                agree, side = sg
                idx = np.flatnonzero(agree & oos)
                w, n_ = sim.run(idx, side[idx], k, j)
                h = stats(n_)
                print(f"  {' + '.join(p):58s} {f'{stops[k]:g}ATR / {targets[j]:g}R':14s} "
                      f"{s['n']:>5} {100 * s['win']:>4.0f}% {s['net']:>+9,.0f} {s['pf']:>4.2f}   "
                      f"{h['n']:>5} {100 * h['win']:>4.0f}% {h['net']:>+9,.0f} {h['pf']:>4.2f} {h['dd']:>8,.0f}")
                report[sym][objective].append({"rule": list(p), "stop_atr": stops[k], "target_r": targets[j],
                                               "in_sample": s, "held_out": h})
            # today's live setup on the same footing, for scale: the engine's call + 5R (stop 2 ATR here)
        base = signal(("engine",))
        for label, k, j in (("engine + 5R (about today's live)", stops.index(2.0), targets.index(5.0)),):
            agree, side = base
            for nm, msk in (("in-sample", ins), ("held-out", oos)):
                idx = np.flatnonzero(agree & msk)
                s = stats(sim.run(idx, side[idx], k, j)[1])
                print(f"  reference - {label}, {nm}: {s['n']} trades, win {100 * s['win']:.0f}%, net {s['net']:+,.0f}, "
                      f"PF {s['pf']:.2f}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cfd_vote_search_result.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1, default=float)
    print("\nsaved", out)


if __name__ == "__main__":
    main()
