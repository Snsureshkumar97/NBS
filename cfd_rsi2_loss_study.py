#!/usr/bin/env python3
"""
cfd_rsi2_loss_study.py — the 87% BTC rule's LOSSES: why they happen, and can they be cut?
================================================================================
The user, 4 Oct 2026, switching the demo forward test to the 87% rule (RSI-2 with ADX >= 25, stop
3 x ATR, one target at 0.2 x the stop): "and also win rate and also look for the why loss and work on
that how to reduce that and make profits with the high win rate".

The rule's trouble is its shape: a win is +0.2 R, a loss -1 R (plus the spread both ways), so one loss
costs ~6 wins. Fewer or smaller losses would pay; a filter or exit that cuts winners with them would not.

EVERY SIGNAL of the rule (every 15-minute close where it says buy or sell) is walked on Exness's
REAL ticks (cfd_outcomes.py's conventions exactly: entry at the close's mid, the stop at the first tick
through it, the target a resting limit, half the spread in and half out, swap on buys) - BOTH sides,
so a coin flip can be scored on the same bars. For each, under several EXIT PLANS:
  today        stop 3 ATR, target 0.6 ATR (0.2 R), out after 24 h
  time N h     the same, but out at the market after N hours if neither was reached (1, 2, 4, 8)
  b/e after N  the same, but after N hours the stop moves to the entry (out at break-even or better)
  stop 2 / 1.5 a tighter stop, the SAME target in ATR (0.6 ATR) - smaller losses, fewer wins
The walker's "today" must equal cfd_outcomes' close-target table on every signal, both sides.

  PART A  WHY LOSSES HAPPEN - every signal under today's exit: how the losers went (how far they got
          first, how long they took) and which conditions at entry they cluster in - in-sample and
          held-out side by side, so a pattern that is only in one period shows as such.
  PART B  EXITS THAT CUT LOSSES - one trade at a time, as live, both periods; KEEP only if better
          than today's net in BOTH.
  PART C  FILTERS FROM PART A - the 3 best single conditions to skip, chosen on the in-sample years
          only, then the held-out year and a coin flip (the same bar as cfd_strict_search: one in
          0.05/3 of coin flips).

    python3 cfd_rsi2_loss_study.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

BAR_NS = 15 * 60 * 10 ** 9
HOLD_NS = 24 * 3600 * 10 ** 9
STALE_NS = 600 * 10 ** 9
H_NS = 3600 * 10 ** 9
TGT_ATR = 0.6                      # 0.2 x a 3-ATR stop - the target stays here in every plan
PLANS = [  # name, stop (x ATR), max hold (hours), break-even after (hours)
    ("today (3 ATR, 24 h)", 3.0, 24, None),
    ("time 1 h", 3.0, 1, None), ("time 2 h", 3.0, 2, None), ("time 4 h", 3.0, 4, None), ("time 8 h", 3.0, 8, None),
    ("b/e after 1 h", 3.0, 24, 1), ("b/e after 2 h", 3.0, 24, 2), ("b/e after 4 h", 3.0, 24, 4),
    ("stop 2 ATR", 2.0, 24, None), ("stop 1.5 ATR", 1.5, 24, None),
]
N_FLIPS = 2000


def _month(args):
    """Every signal bar whose close falls in one month: each plan, both sides -> (idx, net pts, exit ns,
    via, mfe in ATR, hours held) - via 0 target, 1 stop, 2 break-even, 3 time, 4 24 h out."""
    sym, y, m, cand = args
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_outcomes as co
    import cfd_tick_study as t
    import exness_data as ed
    full = ed.load(sym)
    bar_start = full.index.tz_convert("UTC").asi8
    cl, spc = full["Close"].to_numpy(), full["spread_close"].to_numpy()
    atr = co.atr14(full).to_numpy()
    lo = pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC").value
    hi = (pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC") + pd.offsets.MonthBegin(1)).value
    cand = np.asarray(cand, dtype=np.int64)
    idx = cand[(bar_start[cand] + BAR_NS >= lo) & (bar_start[cand] + BAR_NS < hi)]
    P = len(PLANS)
    net = np.full((len(idx), 2, P), np.nan); ext = np.zeros((len(idx), 2, P), np.int64)
    via = np.full((len(idx), 2, P), -1, np.int8); mfe = np.full((len(idx), 2), np.nan); held = np.full((len(idx), 2, P), np.nan)
    parts = [p for p in (t._month_ticks(sym, y, m), t._month_ticks(sym, *((y + 1, 1) if m == 12 else (y, m + 1))))
             if p is not None]
    if not parts or not len(idx):
        return idx, net, ext, via, mfe, held
    T = np.concatenate([p[0] for p in parts]); M = np.concatenate([p[1] for p in parts])
    SP = np.concatenate([p[2] for p in parts])
    for n, i in enumerate(idx):
        t0 = bar_start[i] + BAR_NS
        last = np.searchsorted(T, t0) - 1
        if last < 0 or t0 - T[last] > STALE_NS or not (atr[i] > 0):
            continue
        a, b = np.searchsorted(T, t0), np.searchsorted(T, t0 + HOLD_NS)
        if b <= a:
            continue
        TT = T[a:b]
        for sd, sign in ((0, 1.0), (1, -1.0)):
            f = sign * (M[a:b] - cl[i])
            tgt = TGT_ATR * atr[i]
            g = np.flatnonzero(f >= tgt)
            q_t = g[0] if len(g) else None
            for p, (_, stop_m, hold_h, be_h) in enumerate(PLANS):
                R = stop_m * atr[i]
                ev = []                                           # (index, kind)
                if q_t is not None:
                    ev.append((q_t, 0))
                s = np.flatnonzero(f <= -R)
                if len(s):
                    ev.append((s[0], 1))
                if be_h is not None:
                    k0 = np.searchsorted(TT, t0 + be_h * H_NS)
                    z = np.flatnonzero(f[k0:] <= 0)
                    if len(z):
                        ev.append((k0 + z[0], 2))
                if hold_h < 24:
                    k1 = np.searchsorted(TT, t0 + hold_h * H_NS)
                    if k1 < len(f):
                        ev.append((k1, 3))
                if ev:
                    q, kind = min(ev)
                    pts = tgt if kind == 0 else f[q]
                else:
                    q, kind, pts = len(f) - 1, 4, f[-1]
                net[n, sd, p] = pts - spc[i] / 2 - SP[a + q] / 2
                ext[n, sd, p] = T[a + q] if kind != 4 else T[b - 1]
                via[n, sd, p] = kind
                held[n, sd, p] = (ext[n, sd, p] - t0) / H_NS
                if p == 0:
                    mfe[n, sd] = float(np.max(f[:q + 1])) / atr[i]
    return idx, net, ext, via, mfe, held


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_outcomes as co
    import cfd_rules
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import exness_data as ed
    sym, mult = "BTCUSD", 1.0
    full = ed.load(sym)
    df = full[["Open", "High", "Low", "Close", "Volume"]]
    a = cfd_rules.compute(df)
    rule = np.where((a["rsi2"] != 0) & a["adx25"], a["rsi2"], 0).astype(np.int64)
    cand = np.flatnonzero(rule != 0)
    print(f"{sym}: {len(cand):,} signals of the rule (RSI-2 with ADX >= 25) over 3 years; walking every one on real ticks",
          flush=True)
    jobs = [(sym, y, m, cand.tolist()) for (y, m) in ed.months_until(cts.LAST)]
    n_all, P = len(full), len(PLANS)
    NET = np.full((n_all, 2, P, 1), np.nan, np.float64); EXT = np.zeros((n_all, 2, P, 1), np.int64)
    VIA = np.full((n_all, 2, P), -1, np.int8); MFE = np.full((n_all, 2), np.nan); HELD = np.full((n_all, 2, P), np.nan)
    with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
        for idx, net, ext, via, mfe, held in ex.map(_month, jobs):
            NET[idx, :, :, 0], EXT[idx, :, :, 0], VIA[idx], MFE[idx], HELD[idx] = net, ext, via, mfe, held
    ok = ~np.isnan(NET[:, 0, 0, 0])

    # the walker's "today" must be cfd_outcomes' close-target table, every signal, both sides
    O = np.load(os.path.join(ed._dir(), f"outcomes_close_{sym}.npz"))
    k, j = list(O["stops"]).index(3.0), int(np.argmin(np.abs(O["targets"] - 0.2)))
    ref = O["net"][cand, :, k, j].astype(float)
    mine = NET[cand, :, 0, 0]
    both = ~np.isnan(ref) & ~np.isnan(mine)
    err = float(np.max(np.abs(ref[both] - mine[both]))) if both.any() else float("nan")
    print(f"  check: 'today' vs the close-target table on {int(both.sum()):,} signal-sides - largest difference "
          f"{err:.4f} points, tradable flags agree on {np.mean(np.isnan(ref) == np.isnan(mine)) * 100:.2f}%", flush=True)

    entry_ns = full.index.tz_convert("UTC").asi8 + BAR_NS
    long_sw, _, triple = cts.SWAP[sym]
    rolls, cumw = vs.rollover_weights(entry_ns[0], entry_ns[-1], triple)
    sim = vs.Sim(NET, EXT, ok, entry_ns, long_sw, rolls, cumw, mult)
    ins, oos = entry_ns < vs.SPLIT, entry_ns >= vs.SPLIT
    side_ix = (rule < 0).astype(np.int64)

    def nets_every(idx, p=0):          # $ per BTC of each signal's own trade, swap on buys
        sd = side_ix[idx]
        x = NET[idx, sd, p, 0]
        t1 = EXT[idx, sd, p, 0]
        nights = cumw[np.searchsorted(rolls, t1, side="right") - 1] - cumw[np.searchsorted(rolls, entry_ns[idx], side="right") - 1]
        return (x + np.where(rule[idx] > 0, nights * long_sw / mult, 0.0)) * mult

    def seq(mask, p=0, side=None):
        idx = np.flatnonzero(mask & ok & (rule != 0))
        sd_ = rule[idx] if side is None else side
        return idx, vs.stats(sim.run(idx, sd_, p, 0)[1])

    # ------------------------------------------------------------------ PART A
    sig = np.flatnonzero(ok & (rule != 0))
    v0 = nets_every(sig)
    lose = v0 < 0
    sd = side_ix[sig]
    via0 = VIA[sig, sd, 0]
    print(f"\n{'=' * 112}\n PART A - WHY LOSSES HAPPEN (every signal, today's exit; $ per BTC)\n{'=' * 112}")
    for nm, msk in (("in-sample", ins[sig]), ("held-out", oos[sig])):
        w, l_ = v0[msk & ~lose], v0[msk & lose]
        print(f"  {nm:10s} {msk.sum():>5,} signals, {100 * np.mean(~lose[msk]):.1f}% won; average win {w.mean():+.1f}, "
              f"average loss {l_.mean():+.1f} -> one loss = {abs(l_.mean()) / w.mean():.1f} wins")
        lv = via0[msk & lose]
        print(f"             losses: {100 * np.mean(lv == 1):.0f}% hit the 3-ATR stop, {100 * np.mean(lv == 4):.0f}% still open "
              f"at 24 h, {100 * np.mean(lv == 0):.0f}% hit the target but the spread ate it")
        mf = MFE[sig, sd][msk & lose & (via0 == 1)]
        hl = HELD[sig, sd, 0][msk & lose & (via0 == 1)]
        hw = HELD[sig, sd, 0][msk & ~lose]
        print(f"             stopped-out losers got at most {np.median(mf):.2f} ATR in their favour first (median; the "
              f"target is {TGT_ATR}); {100 * np.mean(mf < 0.1):.0f}% never got 0.1 ATR")
        print(f"             winners took {np.median(hw) * 60:.0f} min (median), {100 * np.mean(hw <= 1):.0f}% within 1 h, "
              f"{100 * np.mean(hw <= 2):.0f}% within 2 h; stopped losers took {np.median(hl):.1f} h")

    # conditions at entry
    c, h, l, o = df["Close"], df["High"], df["Low"], df["Open"]
    t_utc = full.index.tz_convert("UTC")
    ist_h = ((t_utc.hour * 60 + t_utc.minute + 15 + 330) // 60 % 24).to_numpy()   # the close, in IST
    wd = (t_utc + pd.Timedelta(minutes=15 + 330)).weekday.to_numpy()
    d = c.diff()
    rsi2 = (100 - 100 / (1 + cfd_rules._rma(d.clip(lower=0), 2) / cfd_rules._rma((-d).clip(lower=0), 2).replace(0, np.nan))).to_numpy()
    up_, dn_ = h.diff(), -l.diff()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_w = cfd_rules._rma(tr, 14)
    pdi = 100 * cfd_rules._rma(up_.where((up_ > dn_) & (up_ > 0), 0.0), 14) / atr_w
    ndi = 100 * cfd_rules._rma(dn_.where((dn_ > up_) & (dn_ > 0), 0.0), 14) / atr_w
    adx = cfd_rules._rma(100 * (pdi - ndi).abs() / (pdi + ndi), 14).to_numpy()
    atr = a["atr"]
    sma200 = c.rolling(200).mean().to_numpy()
    atr_rel = atr / pd.Series(atr).rolling(96 * 30, min_periods=96).median().to_numpy()
    spread_share = full["spread_close"].to_numpy() / (TGT_ATR * atr)
    drop2 = (rule * (c - c.shift(2)).to_numpy() / atr) * -1        # how far it moved against the trade in 2 bars, in ATR
    dist200 = np.abs(c.to_numpy() - sma200) / atr
    ext_rsi = np.where(rule > 0, rsi2, 100 - rsi2)                 # 0 = the most extreme
    h1 = a["h1_trend"] == rule
    d1 = a["d1_trend"] == rule
    feats = {
        "side": (np.where(rule > 0, "buy", "sell"), ["buy", "sell"]),
        "hour IST (close)": (np.select([ist_h < 6, ist_h < 12, ist_h < 18], ["00-06", "06-12", "12-18"], "18-24"),
                             ["00-06", "06-12", "12-18", "18-24"]),
        "weekend": (np.where(wd >= 5, "Sat/Sun", "Mon-Fri"), ["Mon-Fri", "Sat/Sun"]),
        "ADX": (np.select([adx < 30, adx < 40], ["25-30", "30-40"], "40+"), ["25-30", "30-40", "40+"]),
        "RSI-2 extreme": (np.select([ext_rsi < 2, ext_rsi < 5], ["<2", "2-5"], "5-10"), ["<2", "2-5", "5-10"]),
        "dip in 2 bars": (np.select([drop2 < 1, drop2 < 2], ["<1 ATR", "1-2 ATR"], "2+ ATR"), ["<1 ATR", "1-2 ATR", "2+ ATR"]),
        "from SMA200": (np.select([dist200 < 2, dist200 < 5], ["<2 ATR", "2-5 ATR"], "5+ ATR"), ["<2 ATR", "2-5 ATR", "5+ ATR"]),
        "volatility": (np.select([atr_rel < 0.8, atr_rel < 1.25], ["low", "normal"], "high"), ["low", "normal", "high"]),
        "spread/target": (np.select([spread_share < 0.1, spread_share < 0.2], ["<10%", "10-20%"], "20%+"), ["<10%", "10-20%", "20%+"]),
        "hour trend": (np.where(h1, "with", "against"), ["with", "against"]),
        "day trend": (np.where(d1, "with", "against"), ["with", "against"]),
    }
    print(f"\n  the conditions at entry - share of signals, % LOST, $ per signal (in-sample | held-out):")
    print(f"  {'condition':18s} {'bucket':9s} {'IN-SAMPLE  share  lost%   $/sig':>32s}   {'HELD-OUT  share  lost%   $/sig':>32s}")
    buckets = []
    for fname, (vals, order) in feats.items():
        vv = vals[sig]
        for bk in order:
            cells = []
            for msk in (ins[sig], oos[sig]):
                mm = msk & (vv == bk)
                cells.append(f"{100 * mm.sum() / max(msk.sum(), 1):>8.0f}% {100 * np.mean(lose[mm]) if mm.any() else 0:>6.1f}% "
                             f"{v0[mm].mean() if mm.any() else 0:>+7.1f}")
            print(f"  {fname:18s} {bk:9s} {cells[0]:>32s}   {cells[1]:>32s}")
            buckets.append((fname, bk, vals == bk))

    # ------------------------------------------------------------------ PART B
    print(f"\n{'=' * 112}\n PART B - EXITS THAT CUT LOSSES (one trade at a time, as live; $ per BTC; KEEP = better net in BOTH)\n{'=' * 112}")
    print(f"  {'exit plan':22s} {'IN-SAMPLE   n  win%      net    PF  maxDD':>42s}   {'HELD-OUT   n  win%      net    PF  maxDD':>42s}")
    res = {}
    for p, (name, *_r) in enumerate(PLANS):
        _, si = seq(ins, p)
        _, so = seq(oos, p)
        res[name] = (si, so)
        f_ = lambda s: f"{s['n']:>5} {100 * s['win']:>4.0f}% {s['net']:>+9,.0f} {s['pf']:>5.2f} {s['dd']:>7,.0f}"
        verdict = "" if p == 0 else ("KEEP" if si["net"] > res[PLANS[0][0]][0]["net"] and so["net"] > res[PLANS[0][0]][1]["net"]
                                     else "drop")
        print(f"  {name:22s} {f_(si):>42s}   {f_(so):>42s}  {verdict}", flush=True)

    # ------------------------------------------------------------------ PART C
    print(f"\n{'=' * 112}\n PART C - SKIP THE CONDITIONS LOSERS CLUSTER IN (3 picked on IN-SAMPLE only, then held-out + coin flip)\n{'=' * 112}")
    day = entry_ns // (86400 * 10 ** 9)

    def day_t(mask):
        idx = np.flatnonzero(mask & ok & (rule != 0) & ins)
        if len(idx) < 300:
            return -np.inf
        v = np.nan_to_num(nets_every(idx))
        _, inv = np.unique(day[idx], return_inverse=True)
        sums = np.bincount(inv, weights=v)
        return sums.mean() / (sums.std(ddof=1) / np.sqrt(len(sums)))

    base_t = day_t(np.ones(n_all, bool))
    scored = sorted(((day_t(~m_), f"skip {fn} = {bk}", ~m_) for fn, bk, m_ in buckets), key=lambda x: -x[0])
    print(f"  today's rule: in-sample day-by-day t {base_t:+.2f}. The 3 best skips by the same measure (in-sample only):")
    rng = np.random.default_rng(20261004)
    _, b_is = seq(ins); _, b_oos = seq(oos)
    for tt, name, keep in scored[:3]:
        _, si = seq(ins & keep)
        idx_o, so = seq(oos & keep)
        flips = np.array([sim.run(idx_o, rng.choice([-1, 1], len(idx_o)), 0, 0)[1].sum() for _ in range(N_FLIPS)])
        pc = (1 + np.sum(flips >= so["net"])) / (1 + N_FLIPS)
        better = si["net"] > b_is["net"] and so["net"] > b_oos["net"]
        print(f"  {name:34s} t {tt:+.2f} | in-sample {si['n']:>5} {100 * si['win']:.0f}% {si['net']:>+9,.0f} | held-out "
              f"{so['n']:>4} {100 * so['win']:.0f}% {so['net']:>+9,.0f} PF {so['pf']:.2f} | coin p {pc:.4f} | "
              f"{'better than today in both' if better else 'NOT better in both'}"
              f"{' and PASSES the coin flip' if better and pc <= 0.05 / 3 else ''}", flush=True)
    print(f"  (today, for scale: in-sample {b_is['n']} {100 * b_is['win']:.0f}% {b_is['net']:+,.0f}; held-out {b_oos['n']} "
          f"{100 * b_oos['win']:.0f}% {b_oos['net']:+,.0f})")


if __name__ == "__main__":
    main()
