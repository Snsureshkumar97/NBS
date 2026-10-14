#!/usr/bin/env python3
"""
crypto_tv_ratings_study.py — TradingView's Technical Ratings (a 26-indicator vote) against the tool's crypto, on
3 years of Exness REAL TICKS (BTC and gold)
================================================================================
The user, 14 Oct 2026, after tv_ratings_study.py (the Indian indices): "can you check the same way for crypto like
which other tools use same like voting and i want to compare which is best".

Vote tools for crypto: Cryptohopper's strategy designer opens a trade when a minimum number of the user's chosen
indicators agree (e.g. 2 of 3) - the same idea, but its rules are each user's own, so there is no one strategy to test;
CoinCodex shows a bullish / bearish count of 30+ indicators with no published formula; TradingView's Technical
Ratings - the same 26 votes as on the Indian indices - is the one with published rules, and it runs on BTC and gold.
None publishes a verified accuracy figure.

THE RATINGS: tv_rating.ratings() - the live system's own function (tv_rating.py), on Exness's 15-minute mid candles.
Exness candles carry a tick volume, so VWMA votes here (on an index it has no volume and is left out).

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026), BTC and gold, every exit walked tick by tick, Exness's own spread in
and out, its swap on buys, $ per 1 BTC / per gold lot (100 oz), first two years (to 15 Aug 2025) / held-out year:
  TV OWN     TradingView's built-in Technical Ratings Strategy: Strong Buy -> buy, Strong Sell -> sell, whenever flat at
             a 15-minute close; stop 3 x ATR(14); once 5 x ATR in profit a trailing stop 2 x ATR behind the best tick;
             an opposite Strong signal at a close reverses. 24 hours at most (the tool's CFD limit,
             config.CFD_MAX_HOLD_MINUTES). Three ways - rating All (the main one), MAs only, Oscillators only.
  TV + OURS  TradingView's All rating for the entries; the tool's live crypto exit: the engine's own stop (the
             12-candle swing + 0.15 ATR, 0.75 ATR when the swing is nearer than 0.5 ATR, at most 2 ATR - checked to
             equal the engine's stop on every one of its own trades), one target at 1R (config.CFD_EXIT_PLAN), out
             on an opposite reading, 24 hours at most, 20 minutes before re-entering the same way (as live).
  THE TOOL   the live crypto engine as configured since 6 Oct 2026 (Trend EMA 20/50 + MACD + VWAP, ADX 25, room to
             run, plain 1R) - crypto_engine_votes_study.py's own walk, asserted to reproduce its figures.
"Accuracy" two ways: trades won after costs, and how often price had moved the call's way an hour later.

ADDED AFTER THE FIRST RUN: each line split into buys and sells - gold rose through both periods, and a system that
mostly buys looks good in a rally.

RESULT (14 Oct 2026, ticks to Aug 2026; crypto_tv_ratings_study_results.txt), first two years / held-out:
  BTC ($ per BTC): THE TOOL -34,831 / -3,057 (51% / 50% won). TradingView's own strategy -26,552 / +26,421 (34% / 36%)
    - less bad than the tool both periods, still a loss in the first two years; MAs only -59,602 / -15,806; TV votes
    + the tool's exit -37,126 / -7,690. Nothing profitable in both periods; the retired RSI-2 rule (+25,729 / +25,228)
    stays the best BTC result on record.
  GOLD ($ per lot): THE TOOL -30,347 / -33,445. TradingView's own strategy -3,105 / +194,423; MAs only +48,403 /
    +145,212 (PF 1.06 / 1.11, worst drop 29,508 / 74,577) - the one setup here profitable in both periods - but in the
    first two years its profit was the buys (+69,963) while its sells lost (-21,560): gold's rally; in the held-out year
    both made money (buys +105,531, sells +39,681). TV votes + the tool's exit -26,329 / -37,955. The retired gold
    trend rule: +108,722 / +47,774. No coin-flip test run; earlier gold searches failed it on the rally.
  Oscillators only reach Strong almost never (2 BTC, 9 gold trades in 3 years). "Right an hour later": 45-50% for
  every system, the tool included - no better than a coin.
  BUILT for gold the same day (the user: "change gold to trading view moving averages"; config.CFD_RULES["GOLD"],
  cfd_rules._apply_tv): "MAs only, as built" - with the 30 x ATR safety target every Exness order needs - is the same
  trade for trade, +48,403 / +145,212: the target was never reached in 3 years. BTC unchanged (the engine).

    python3 crypto_tv_ratings_study.py
"""
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SYMBOLS = {"BTCUSD": ("BTC", 1.0), "XAUUSD": ("GOLD", 100.0)}
STRONG = 0.5
TOOL_FIGS = {"BTCUSD": (-34831, -3057), "XAUUSD": (-30347, -33445)}   # crypto_engine_votes_study_results.txt, plain 1R
RETIRED = {"BTCUSD": ("RSI-2 rule (retired 6 Oct)", 25729, 25228), "XAUUSD": ("gold trend rule (retired 6 Oct)", 108722, 47774)}
VARIANTS = ("tv_all", "tv_ma", "tv_osc", "tv_ours", "tv_ma_tp")
# added 14 Oct 2026 when the user chose the MAs-only rating for gold: the same with a far take-profit, 30 x ATR - the
# Exness executor never sends an order without a target resting at Exness (the 4 Oct incident guard), so the live
# rule carries one, set before this was run where it should almost never be reached inside 24 hours
TP_ATR = 30.0


def engine_stop(pre, i, side, entry):
    """signal_engine.build_recommendation's stop at candle i (config.SWING_LOOKBACK swing, SL_BUFFER_ATR_MULT,
    MIN/MAX_RISK_ATR_MULT, SL_ATR_MULT fallback)."""
    import config
    atr = float(pre["atr"][i])
    min_risk, max_risk = atr * config.MIN_RISK_ATR_MULT, atr * config.MAX_RISK_ATR_MULT
    if side == "CE":
        sw = float(pre["swing_low"][i])
        sl = round(sw - atr * config.SL_BUFFER_ATR_MULT, 2) if sw < entry and entry - sw >= min_risk \
            else round(entry - atr * config.SL_ATR_MULT, 2)
        if entry - sl > max_risk:
            sl = round(entry - max_risk, 2)
        if entry - sl <= 0:
            sl = round(entry - atr * config.SL_ATR_MULT, 2)
    else:
        sw = float(pre["swing_high"][i])
        sl = round(sw + atr * config.SL_BUFFER_ATR_MULT, 2) if sw > entry and sw - entry >= min_risk \
            else round(entry + atr * config.SL_ATR_MULT, 2)
        if sl - entry > max_risk:
            sl = round(entry + max_risk, 2)
        if sl - entry <= 0:
            sl = round(entry + atr * config.SL_ATR_MULT, 2)
    return sl


def _prep(sym):
    """Every candidate entry of every TradingView variant (each Strong close), the per-candle TradingView reading for
    the reversal exit, and the engine-stop check. Cached beside the Exness data."""
    import backtest_intraday as bt
    import exness_data as ed
    import tv_rating
    key = SYMBOLS[sym][0]
    df = ed.load(sym)[["Open", "High", "Low", "Close", "Volume"]]
    path = os.path.join(ed._dir(), f"tvratings2_{sym}_{len(df)}.pkl")
    if os.path.exists(path):
        return sym, path
    rt = tv_rating.ratings(df)
    pre = {k: np.asarray(v) for k, v in bt.precompute(df, key).items() if k in ("atr", "swing_low", "swing_high")}
    cl = df["Close"].to_numpy()
    side_of = {b: np.where(rt[b].to_numpy() > STRONG, "CE", np.where(rt[b].to_numpy() < -STRONG, "PE", None))
               for b in ("all", "ma", "osc")}
    atr_tv = rt["atr"].to_numpy()
    cands = {v: [] for v in VARIANTS}
    for v, basis in (("tv_all", "all"), ("tv_ma", "ma"), ("tv_osc", "osc"), ("tv_ours", "all"), ("tv_ma_tp", "ma")):
        sides = side_of[basis]
        for i in range(250, len(df) - 1):
            s = sides[i]
            if s is None:
                continue
            e = float(cl[i])
            if v == "tv_ours":
                stop = engine_stop(pre, i, s, e)
            else:
                stop = e - 3 * atr_tv[i] if s == "CE" else e + 3 * atr_tv[i]
            cands[v].append({"i": i, "side": s, "entry": e, "stop": float(stop), "atr": float(atr_tv[i])})
    # the engine's stop, recomputed for the engine's own trades, must be the stop it set
    with open(os.path.join(ed._dir(), [f for f in os.listdir(ed._dir()) if f.startswith(f"votes_{sym}_tmv_")][0]), "rb") as fh:
        tool = pickle.load(fh)["entries"]
    pos = {t: n for n, t in enumerate(df.index)}
    mism = [tr["when"] for tr in tool if abs(engine_stop(pre, pos[tr["when"]], tr["side"], tr["entry"]) - tr["stop"]) > 0.011]
    with open(path, "wb") as fh:
        pickle.dump({"cands": cands, "opp": {b: list(side_of[b]) for b in side_of}, "stop_check": (len(tool), len(mism))}, fh)
    return sym, path


def walk_trail(T, M, S, bar_start, cl, i, side, entry, stop, act, off, opp, max_bars=96, target=None):
    """TradingView's own exit on ticks: the fixed stop, and once the best tick is `act` in profit a trailing stop `off`
    behind the best tick (Pine's trail_points / trail_offset); an opposite Strong reading at a close -> out at that
    close (the reversal); 24 hours at most. -> (exit price, exit candle, via, exit spread or nan)."""
    import cfd_tick_study as t
    ce = side == "CE"
    best = entry
    n = len(cl)
    for j in range(i + 1, min(i + 1 + max_bars, n)):
        a, b = np.searchsorted(T, bar_start[j]), np.searchsorted(T, bar_start[j] + t.BAR_NS)
        mid = M[a:b]
        if len(mid):
            if ce:
                run = np.maximum.accumulate(np.maximum(mid, best))
                eff = np.where(run - entry >= act, np.maximum(stop, run - off), stop)
                hit = t.first(mid <= eff)
                g_hit = t.first(mid >= target) if target is not None else None
            else:
                run = np.minimum.accumulate(np.minimum(mid, best))
                eff = np.where(entry - run >= act, np.minimum(stop, run + off), stop)
                hit = t.first(mid >= eff)
                g_hit = t.first(mid <= target) if target is not None else None
            if g_hit is not None and (hit is None or g_hit < hit):
                return target, j, "target", S[a + g_hit]
            if hit is not None:
                return mid[hit], j, ("trail" if eff[hit] != stop else "stop"), S[a + hit]
            best = run[-1]
        if opp is not None and opp[j] is not None and opp[j] != side:
            last = mid[-1] if len(mid) else cl[j]
            return last, j, "reverse", (S[b - 1] if b > a else np.nan)
    j = min(i + max_bars, n - 1)
    return cl[j], j, "other", np.nan


def _job(args):
    sym, y, m, path = args
    import backtest_intraday as bt
    import cfd_tick_study as t
    import exness_data as ed
    key, mult = SYMBOLS[sym]
    full = ed.load(sym)
    df = full[["Open", "High", "Low", "Close", "Volume"]]
    with open(path, "rb") as fh:
        d = pickle.load(fh)
    t_lo = pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC")
    t_hi = t_lo + pd.offsets.MonthBegin(1)
    idx_utc = df.index.tz_convert("UTC")
    lo_i, hi_i = int(idx_utc.searchsorted(t_lo)), int(idx_utc.searchsorted(t_hi))
    parts = [p for p in (t._month_ticks(sym, y, m), t._month_ticks(sym, *((y + 1, 1) if m == 12 else (y, m + 1))))
             if p is not None]
    if not parts:
        return []
    T = np.concatenate([p[0] for p in parts]); M = np.concatenate([p[1] for p in parts])
    S = np.concatenate([p[2] for p in parts])
    adx_arr = bt.precompute(df, key)["adx"].to_numpy()
    cl = df["Close"].to_numpy()
    sp_c = full["spread_close"].to_numpy()
    bar_start = idx_utc.asi8
    out = []
    for v in VARIANTS:
        opp = d["opp"]["all" if v in ("tv_all", "tv_ours") else "ma" if v == "tv_ma_tp" else v[3:]]
        for c in d["cands"][v]:
            i = c["i"]
            if not (lo_i <= i < hi_i):
                continue
            if v == "tv_ours":
                risk = abs(c["entry"] - c["stop"])
                tgt = c["entry"] + risk if c["side"] == "CE" else c["entry"] - risk
                px, j, via, xs = t.walk(T, M, S, bar_start, cl, i, c["side"], c["entry"], c["stop"], None, tgt,
                                        None, opp, ratchet=False, trail=False, be_bars=0)
            else:
                tp = (None if v != "tv_ma_tp" else
                      c["entry"] + TP_ATR * c["atr"] if c["side"] == "CE" else c["entry"] - TP_ATR * c["atr"])
                px, j, via, xs = walk_trail(T, M, S, bar_start, cl, i, c["side"], c["entry"], c["stop"],
                                            5 * c["atr"], 2 * c["atr"], opp, target=tp)
            xs = sp_c[j] if not (xs == xs) else xs
            sign = 1 if c["side"] == "CE" else -1
            net = (sign * (px - c["entry"]) - sp_c[i] / 2 - xs / 2) * mult
            out.append({"variant": v, "i": i, "when": df.index[i] + pd.Timedelta(minutes=15),
                        "exit_time": df.index[j] + pd.Timedelta(minutes=15), "side": c["side"],
                        "closed_via": "target" if via == "target" else ("stop" if via == "stop" else "other"),
                        "how": via, "exit_adx": float(adx_arr[j]), "net": net})
    return out


def _tool_job(args):
    """The live crypto engine's own trades for one symbol-month: crypto_engine_votes_study's walk, plain 1R only."""
    import crypto_engine_votes_study as cev
    cev.VARIANTS = {"plain 1R (new live)": cev.VARIANTS["plain 1R (new live)"]}      # set in this worker
    return cev._job(args)


def add_swap_and_direction(rows, sym, df):
    import cfd_tick_study as t
    long_sw, short_sw, triple = t.SWAP[sym]
    cl = df["Close"].to_numpy()
    pos = {x: n for n, x in enumerate(df.index)}
    for r in rows:
        k = t.rollovers(r["when"], r["exit_time"], triple)
        r["net"] = r["net"] + k * (long_sw if r["side"] == "CE" else short_sw)
        i = pos[r["when"] - pd.Timedelta(minutes=15)]
        mv = cl[min(i + 4, len(cl) - 1)] - cl[i]
        r["right_1h"] = (mv > 0) if r["side"] == "CE" else (mv < 0)


def main():
    from concurrent.futures import ProcessPoolExecutor
    import cfd_tick_study as t
    import config
    import crypto_engine_votes_study as cev
    import crypto_strategy_study as cs
    import exness_data as ed
    print("Reading TradingView's ratings on Exness's candles and walking every trade on real ticks...", flush=True)
    with ProcessPoolExecutor(max_workers=2) as ex:
        paths = dict(ex.map(_prep, list(SYMBOLS)))
        tool_paths = dict(ex.map(cev._entries, list(SYMBOLS)))
    months = ed.months_until(t.LAST)
    workers = max(1, min(6, (os.cpu_count() or 2) - 1))
    got = {}
    only = [x for x in os.environ.get("STUDY_SYMBOLS", "").split(",") if x]
    for sym in [x for x in SYMBOLS if not only or x in only]:
        df = ed.load(sym)[["Open", "High", "Low", "Close", "Volume"]]
        with open(paths[sym], "rb") as fh:
            n_tool, n_mism = pickle.load(fh)["stop_check"]
        assert n_mism == 0, f"{sym}: the engine stop differs from the engine's own on {n_mism} of {n_tool} trades"
        rows, tool_rows = [], []
        with ProcessPoolExecutor(workers) as ex:
            for part in ex.map(_job, [(sym, y, m, paths[sym]) for (y, m) in months]):
                rows.extend(part)
            for part in ex.map(_tool_job, [(sym, y, m, tool_paths[sym]) for (y, m) in months]):
                tool_rows.extend(part)
        add_swap_and_direction(rows, sym, df)
        add_swap_and_direction(tool_rows, sym, df)
        res = {"tool": cs.sequential(tool_rows, cooldown_min=config.REENTRY_COOLDOWN_MIN,
                                     waiver_adx=config.ADX_TREND_THRESHOLD)}
        for v in VARIANTS:
            vr = [r for r in rows if r["variant"] == v]
            res[v] = (cs.sequential(vr, cooldown_min=config.REENTRY_COOLDOWN_MIN, waiver_adx=config.ADX_TREND_THRESHOLD)
                      if v == "tv_ours" else cs.sequential(vr))
        p = cs.periods(res["tool"])
        a, b = TOOL_FIGS[sym]
        assert (round(cs.stats(p["is"], "net")["total"]), round(cs.stats(p["oos"], "net")["total"])) == (a, b), \
            f"{sym}: the tool's rows must be crypto_engine_votes_study's plain 1R"
        got[sym] = (res, n_tool)
    label = {"tool": "THE TOOL as it runs today", "tv_all": "TradingView strategy, rating All",
             "tv_ma": "TradingView strategy, MAs only", "tv_osc": "TradingView strategy, oscillators only",
             "tv_ours": "TradingView All votes + the tool's exit",
             "tv_ma_tp": "MAs only, as built (target 30 x ATR)"}
    W = 176
    for sym, (res, n_tool) in got.items():
        key, _ = SYMBOLS[sym]
        unit = "1 BTC" if key == "BTC" else "gold lot"
        print("\n" + "=" * W)
        print(f" EXNESS {sym} ON REAL TICKS - TradingView's Technical Ratings vs the tool; $ per {unit}, after spread and"
              " swap.  columns: trades, won, total, profit factor, worst drop | right 1h = moved the call's way an hour later")
        print(f" (the engine's stop, recomputed, equals its own on all {n_tool} of its trades)")
        print("=" * W)
        print(f" {'':44s} {'FIRST TWO YEARS (to 15 Aug 2025)':48s} | {'HELD-OUT YEAR':48s} | right 1h")

        def c(rs):
            s = cs.stats(rs, "net")
            return f"{s['n']:>5} {s['win']:>4.0f}% ${s['total']:>+11,.0f} PF {s['pf']:4.2f} DD ${s['dd']:>9,.0f}"
        for v in ("tool",) + VARIANTS:
            p = cs.periods(res[v])
            acc = lambda x: 100.0 * sum(r["right_1h"] for r in x) / len(x) if x else 0.0
            print(f"   {label[v]:42s} {c(p['is'])} | {c(p['oos'])} | {acc(p['is']):3.0f}% / {acc(p['oos']):3.0f}%")
        rn, ra, rb = RETIRED[sym]
        print(f"   {'(for scale) ' + rn:42s} {'':>5} {'':>4}  ${ra:>+11,}{'':27s} | {'':>5} {'':>4}  ${rb:>+11,}")
        print("  VERDICT - better than the tool only if more in BOTH periods")
        base = cs.periods(res["tool"])
        for v in VARIANTS:
            p = cs.periods(res[v])
            di = cs.stats(p["is"], "net")["total"] - cs.stats(base["is"], "net")["total"]
            do = cs.stats(p["oos"], "net")["total"] - cs.stats(base["oos"], "net")["total"]
            both = cs.stats(p["is"], "net")["total"] > 0 and cs.stats(p["oos"], "net")["total"] > 0
            print(f"   {'KEEP' if di > 0 and do > 0 else 'drop'}  {label[v]:42s} vs the tool: {di:>+11,.0f} / {do:>+11,.0f}"
                  f"   profitable both periods: {'yes' if both else 'no'}")
        # added after the first run (14 Oct 2026): gold rallied through the held-out year, so is a lead just the buys?
        print("  BUYS vs SELLS (first two years / held-out) - a system that mostly buys looks good in a rally")
        for v in ("tool",) + VARIANTS:
            p = cs.periods(res[v])
            part = lambda rs, s: cs.stats([r for r in rs if r["side"] == s], "net")
            fmt = lambda st: f"{st['n']:>5} ${st['total']:>+10,.0f}"
            print(f"   {label[v]:42s} buys {fmt(part(p['is'], 'CE'))} / {fmt(part(p['oos'], 'CE'))}"
                  f"   sells {fmt(part(p['is'], 'PE'))} / {fmt(part(p['oos'], 'PE'))}")
        from collections import Counter
        print("   how TradingView's own strategy (All) closed: " + ", ".join(
            f"{w}: {n}" for w, n in Counter(r["how"] for r in res["tv_all"]).most_common()))
    return got


if __name__ == "__main__":
    main()
