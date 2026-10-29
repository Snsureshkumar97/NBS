#!/usr/bin/env python3
"""
debit_spread_study.py — the tool's own trades as DEBIT SPREADS: buy the at-the-money option, sell the target strike
================================================================================
The user, 29 Oct 2026: "yes start the debit spread test" (after "advice me ... to improve the tool startegy": the
tool's direction has a small real edge - futures_study - that buying the nearest weekly option loses to time decay
and costs; a spread pays about half as much for the same move, and the tool already exits at its targets).

FIXED BEFORE ANY RESULT WAS SEEN (29 Oct 2026). Every trade of each system as honest_retest.py's harness makes it
(stops filled honestly, one ticket at a time per index, cooldowns as live), booked four ways at the same entry and
exit minutes, per lot, after every charge and 0.25% slippage a leg (regime_study.net_rupees on each leg):
  NAKED     buy the at-the-money option (what the tool does)
  T1/T2/T3  the same, plus SELL the same expiry's strike nearest that target level (at least one strike further out
            than the bought one), both legs closed together. The big candle has one target: all three sell at it.
            A sold leg priced under Rs 1 is not worth selling: that trade stays NAKED.
Systems: the user's setup (the TradingView rating with yesterday's levels, sr_study 1.5x, + the big candle; Midcap
the rating with the levels), the same one trade a day (oneperday_study), and the rating alone. Six years on the
corrected model (pricing.py), at the nearest expiry and at least 14 days out (pricing.MIN_DTE). Then the recorder's
REAL prices, 10 Sep - 26 Oct 2026 (nearest and next recorded expiry): the bought leg as real_price_check.real_net
does, the sold leg from its own recorded minutes - and only from a candle at most 5 minutes old (a far strike
trades less; a stale price must not flatter it).
A spread is BETTER only if it beats NAKED in BOTH periods (to 15 Aug 2025, then held out) pooled over the four
indices, holds up on robustness.py's walk-forward / Monte Carlo, is no worse than NAKED on the real prices, and the
model does not flatter it there more than it flatters NAKED (the model over-prices far strikes -
nbs-high-win-strategy-research).

RESULT (29 Oct 2026; debit_spread_study_results.txt). DROP - every spread, every system, both expiries, both periods,
walk-forward 0-4 of 13. Per lot, the four pooled, to 15 Aug 2025 / held out, the user's setup:
  nearest   NAKED +160,000 / +152,775   T1 -266,335 / -103,713   T2 -140,757 / -49,958   T3 -98,062 / -14,422
  14+ days  NAKED +954,607 / +371,902   T1 -900,278 / -292,815   T2 -678,910 / -215,185   T3 -499,429 / -140,653
  one a day, nearest: NAKED +214,527 / +141,070, the closest spread T3 +2,061 / +22,886 (-212k / -118k on NAKED).
  The rating alone the same (nearest NAKED -485,442 / +62,487, T3 -846,848 / -282,653).
Why: the win rate falls (39% -> 27-33% at the nearest, 47% -> 15-23% at 14+ days) - small winners turn into losers
under the second leg's charges and slippage; a stop-out loses barely less (the sold strike was cheap); and a winner
is cut, because the tool reaches its target hours before expiry, when the sold option still holds much of its value.
Inside one day the decay a spread saves is small (at 14+ days tiny), so it halves the move and doubles the costs.
REAL PRICES (10 Sep - 26 Oct, the same trades): mostly worse too - the setup at the nearest expiry T2 +14,136 against
NAKED +15,707 (46 trades); on the next recorded expiry every spread far worse (T2 -25,577 against -1,468). Where a
spread lost less (T3 at the nearest, -5,871 against -16,262 on 38) only the trades whose far T3 strike happened to be
recorded are priced - mostly losers. The model on those same trades agrees with the real prices (it does not flatter
the spreads), so the six-year verdict stands.

    HOME=<six-year history> /usr/bin/python3 debit_spread_study.py [OPTION_HISTORY_DIR]
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
WAYS = ("NAKED", "T1", "T2", "T3")
SLIP = 0.0025
MIN_SHORT = 1.0          # rupees: a sold leg cheaper than this is not sold
FRESH_MIN = 5            # minutes: the sold leg's recorded price must be this fresh


def short_strike(K, level, step, call):
    """The strike nearest the target level, at least one step further out than the bought strike K."""
    Ks = round(level / step) * step
    return max(Ks, K + step) if call else min(Ks, K - step)


def model_net(r, days, way):
    """Net rupees per lot on pricing.py's corrected model; None if the bought option is worthless at entry."""
    import config
    import pricing
    import pro_study as ps
    import regime_study as rs
    k = r["index"]
    meta = config.INSTRUMENTS[k]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    t_in, t_out = pd.Timestamp(r["when"]), pd.Timestamp(r["exit_time"])
    exp = ps.trade_expiry(k, t_in.date(), days)
    call = r["side"] == "CE"
    K = round(r["entry_spot"] / step) * step
    p0 = pricing.premium(r["entry_spot"], K, exp, t_in, r["sigma"], call)
    if p0 <= 0.5:
        return None
    p1 = pricing.premium(r["exit_spot"], K, exp, t_out, r["sigma"], call)
    net = rs.net_rupees(p0, p1, qty, exch, SLIP)
    if way == "NAKED":
        return net
    Ks = short_strike(K, r[way.lower()], step, call)
    q0 = pricing.premium(r["entry_spot"], Ks, exp, t_in, r["sigma"], call)
    if q0 < MIN_SHORT:
        return net
    q1 = pricing.premium(r["exit_spot"], Ks, exp, t_out, r["sigma"], call)
    return net + rs.net_rupees(q1, q0, qty, exch, SLIP)       # sold at q0, bought back at q1


def fresh(s, t, at):
    """The recorded price at minute t - its open ("open"), or the last close up to t ("close") - from a candle at
    most FRESH_MIN minutes old; else None."""
    if s is None:
        return None
    if at == "open" and t in s.index:
        return float(s.loc[t, "open"])
    lo = t - pd.Timedelta(minutes=FRESH_MIN)
    win = s[(s.index >= lo) & ((s.index < t) if at == "open" else (s.index <= t))]
    return float(win["close"].iloc[-1]) if len(win) else None


def real_net(r, P, which, way):
    """The trade on recorded prices (which: 0 the nearest expiry, 1 the next recorded one) -> net per lot, or None
    (no recording, no price, or - for a spread - no fresh price for the sold leg)."""
    import config
    import live_vs_backtest as lv
    import regime_study as rs
    k = r["index"]
    meta = config.INSTRUMENTS[k]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    t_in, t_out = pd.Timestamp(r["when"]), pd.Timestamp(r["exit_time"])
    d = t_in.date().isoformat()
    exps = [e for e in P.expiries(k, d) if e >= d]
    if len(exps) <= which:
        return None
    exp = exps[which]
    K = round(r["entry_spot"] / step) * step
    opt = P.option(k, d, exp, K, r["side"])
    p0 = lv.at_open(opt, t_in)
    if p0 is None or p0 <= 0.5:
        return None
    exit_t = t_out - pd.Timedelta(minutes=1)                  # as real_price_check.real_net
    if r["how"] == "level":
        idx = P.index(k, d)
        if idx is not None and len(idx):
            win = idx[(idx.index >= t_out - pd.Timedelta(minutes=15)) & (idx.index < t_out)]
            hit = win[(win["low"] <= r["exit_spot"]) & (win["high"] >= r["exit_spot"])]
            if len(hit):
                exit_t = hit.index[0]
            elif len(win):
                exit_t = win.index[0]
    p1 = lv.at_close(opt, exit_t)
    if p1 is None:
        return None
    net = rs.net_rupees(p0, p1, qty, exch, SLIP)
    if way == "NAKED":
        return net
    Ks = short_strike(K, r[way.lower()], step, r["side"] == "CE")
    sh = P.option(k, d, exp, Ks, r["side"])
    q0, q1 = fresh(sh, t_in, "open"), fresh(sh, exit_t, "close")
    if q0 is None or q1 is None:
        return None
    if q0 < MIN_SHORT:
        return net
    return net + rs.net_rupees(q1, q0, qty, exch, SLIP)


def _job(job):
    """(key, system) -> the system's trades with their stop and targets, and the model's net every way, nearest
    expiry (m0) and 14+ days (m14)."""
    key, system = job
    import backtest_intraday as bt
    import pricing
    seen, out, lost = {}, [], 0
    if system in ("TV", "TVL"):
        import real_price_check as rpc
        import tv_ratings_study as tvs
        real = tvs.walk

        def walk(bars, t, start, bar_min, side, entry, stop, target, rungs, *a, **kw):
            if target is not None and len(rungs) >= 2:         # the tool's exit: T1, T2 the rungs, T3 the target
                seen[(pd.Timestamp(t[start]), side)] = (stop, rungs[0][0], rungs[1][0], target)
            return real(bars, t, start, bar_min, side, entry, stop, target, rungs, *a, **kw)
        tvs.walk = walk
        rows = rpc._tv_levels(key)[1] if system == "TVL" else rpc._job((key, "TV"))[1]
        for r in rows:
            got = seen.get((pd.Timestamp(r["when"]), r["side"]))
            if got is None:
                lost += 1
                continue
            out.append(dict(r, index=key, stop=got[0], t1=got[1], t2=got[2], t3=got[3]))
    else:
        import big_candle_study as bcs
        import indian_strategies_study as iss
        import one_target_study as ots
        real_walk, real_opt = iss.walk, iss.opt_net

        def walk(bars, start, side, stop, target, custom_exit=None, last="15:15"):
            px, j = real_walk(bars, start, side, stop, target, custom_exit, last)
            seen["how"] = "close" if px == float(bars["Close"].iloc[j]) else "level"
            seen["lv"] = (stop, target)
            return px, j

        def opt_net(k, days, t_in, t_out, s_in, s_out, side, sigma):
            seen[t_in] = (s_in, s_out, seen.get("how", "level"), seen.get("lv"), sigma)
            return real_opt(k, days, t_in, t_out, s_in, s_out, side, sigma)
        iss.walk, iss.opt_net = walk, opt_net
        for r in bcs.run(key, ots.BC):
            s_in, s_out, how, lv, sigma = seen[r["when"]]
            if lv is None or lv[1] is None or not sigma or sigma != sigma:
                lost += 1
                continue
            out.append(dict(r, index=key, exit_time=r["out"], entry_spot=s_in, exit_spot=s_out, how=how,
                            stop=lv[0], t1=lv[1], t2=lv[1], t3=lv[1], sigma=float(sigma)))
    days = set(bt.fetch_history(key, years=3, use_cache=True).index.date)
    for m in (0, 14):
        pricing.MIN_DTE = m
        for r in out:
            r[f"m{m}"] = {w: model_net(r, days, w) for w in WAYS}
    pricing.MIN_DTE = 0
    return job, (out, lost)


def systems(got):
    """{name: [rows]} pooled over the four indices: the user's setup, the same one a day, the rating alone."""
    import oneperday_study as opd
    import order_block_site_study as obs
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    setup, tv = [], []
    for k in KEYS:
        tl, bc = s(got[(k, "TVL")][0]), s(got[(k, "BC")][0])
        setup += s(obs.slot(tl, bc)) if k != "MIDCPNIFTY" else tl
        tv += got[(k, "TV")][0]
    return {"YOUR SETUP (rating + levels + big candle)": s(setup),
            "YOUR SETUP, one trade a day": s(opd.first_per_day(setup)),
            "the TradingView rating alone": s(tv)}


def as_rows(rows, m, way):
    """The trades whose bought option is priced, with net = that way's (the same trades for every way)."""
    return [dict(r, when=pd.Timestamp(r["when"]), net=r[m][way]) for r in rows if r[m]["NAKED"] is not None]


def main():
    import multiprocessing as mp
    import live_vs_backtest as lv
    import stop_day_study as sds
    jobs = [(k, sy) for k in KEYS for sy in ("TV", "TVL", "BC")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    lost = {j: v[1] for j, v in got.items() if v[1]}
    print(f"trades without their targets captured (left out): {lost or 'none'}")
    tv0 = [r for k in KEYS for r in got[(k, "TV")][0] if r["m0"]["NAKED"] is not None]
    gap = max((abs(r["m0"]["NAKED"] - r["net"]) for r in tv0), default=0.0)
    print(f"check: NAKED at the nearest expiry against the harness's own price, the rating's {len(tv0)} trades - "
          f"largest difference Rs {gap:,.2f}")
    S = systems(got)
    for name, rows in S.items():
        for m, label in (("m0", "NEAREST EXPIRY"), ("m14", "14+ DAYS TO EXPIRY")):
            print("=" * 172)
            print(f"{name.upper()} - {label}, the model, all four pooled, per lot   (to 15 Aug 2025 | held out)")
            base = as_rows(rows, m, "NAKED")
            for w in WAYS:
                print(sds.line(f"   {w if w == 'NAKED' else 'spread, sell at ' + w}", as_rows(rows, m, w)))
            for w in WAYS[1:]:
                text = sds.verdict(f"spread at {w} vs naked", as_rows(rows, m, w), base).strip()
                print("   " + text.replace("\n", "\n   "))
            by = lambda w, k: sum(r["net"] for r in as_rows(rows, m, w) if r["index"] == k)
            print("   by index, six years:  " + "   ".join(
                f"{k} " + " / ".join(f"{w} {by(w, k):+,.0f}" for w in WAYS) for k in KEYS))
    if len(sys.argv) < 2:
        return
    import real_price_check as rpc
    P = lv.Prices(sys.argv[1])
    print("=" * 172)
    print(f"REAL RECORDED PRICES, {rpc.SINCE.date()} to {(rpc.UNTIL - pd.Timedelta(days=1)).date()} - per lot, after "
          f"charges and 0.25% slippage a leg; each spread against NAKED on the SAME trades (those its sold leg has a "
          f"fresh price for), and the model on those same trades")
    for name, rows in S.items():
        win = [r for r in rows if rpc.SINCE <= pd.Timestamp(r["when"]) < rpc.UNTIL]
        print(f"{name}  ({len(win)} trades)")
        for which, label in ((0, "nearest"), (1, "next recorded")):
            naked = {id(r): real_net(r, P, which, "NAKED") for r in win}
            n_ok = [v for v in naked.values() if v is not None]
            print(f"   {label:14s} NAKED {len(n_ok):4d} priced {sum(n_ok):+9,.0f}  "
                  f"({100 * sum(v > 0 for v in n_ok) / max(len(n_ok), 1):.0f}% won)")
            for w in WAYS[1:]:
                pairs = [(naked[id(r)], real_net(r, P, which, w), r) for r in win if naked[id(r)] is not None]
                pairs = [(a, b, r) for a, b, r in pairs if b is not None]
                mod = [(r["m0"]["NAKED"], r["m0"][w]) for _, _, r in pairs
                       if which == 0 and r["m0"]["NAKED"] is not None]
                ms = (f" | the model on those: naked {sum(a for a, _ in mod):+9,.0f} spread {sum(b for _, b in mod):+9,.0f}"
                      if mod else "")
                print(f"      sell at {w}: {len(pairs):4d} priced  spread {sum(b for _, b, _ in pairs):+9,.0f} "
                      f"({100 * sum(b > 0 for _, b, _ in pairs) / max(len(pairs), 1):.0f}% won)  naked on the same "
                      f"{sum(a for a, _, _ in pairs):+9,.0f}" + ms)


if __name__ == "__main__":
    main()
