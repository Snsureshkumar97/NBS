#!/usr/bin/env python3
"""
stop_day_study.py — three changes suggested by 7 Oct 2026's losing morning, on 3 years
================================================================================
The user, 7 Oct 2026: "what mistake happened today we hit our stop loss what change we can do to avoid these".

THE DAY: 6 Oct closed at its high (Nifty 22,776). 7 Oct opened lower (22,690) and fell another 90 points in the
first minute, then went sideways for an hour and shot back up at 10:15. The tool bought PUTS on all three indices -
Nifty at 09:22, Sensex 09:40, Bank Nifty 09:41 (the last two at the morning's low) - and all three hit their stops
10:06-10:29. 5 Oct was the mirror image: calls bought 09:21-09:32 into an opening pop, then a 1-1.5% fall.

THE THREE CHANGES (each from something seen that morning, each tested on its own):
  A. one bet, not three - at most `cap` open trades in the SAME direction across the three indices (they move
     together: three puts on one move is one bet three times). Same entries; a trade is skipped while `cap` others
     in its direction are open on the other indices.
  B. don't chase the opening move - on a day whose first 15-minute candle closed more than g% from yesterday's close,
     no entry IN THAT DIRECTION from a candle starting before the cutoff. Swept g and the cutoff to see whether any
     result is a pattern or one lucky cell.
  C. ADX must be rising - the 15-minute ADX higher than one candle earlier (7 Oct's 21-26 at entry was mostly the
     opening candle; it was 15 at the stops).

Everything else exactly as opening_window_study.py (read its header): the live entry checks, the live exit with the
2-hour breakeven priced as it fills, one position per index, the cooldown waiver, per lot after costs, real expiries.
A change is KEPT only if its total beats today's in BOTH periods (in-sample to 15 Aug 2025, then the held-out final
year); the worst drop and the worst DAY are shown because change A is about risk, not profit.

    python3 stop_day_study.py
"""
import datetime as dt
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import conditional_cooldown_study as ccs
import config
import indicators as ind
import opening_window_study as ows
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res

INDICES = rs.INDICES
SPLIT = rs.SPLIT
GAPS = (0.003, 0.005, 0.0075)
CUTOFFS = (dt.time(9, 45), dt.time(10, 15), dt.time(10, 45))


def raw_candidates(k, df, gate, A, F, pre, st, target):
    """ows.candidates() before the one-position-per-index pass (so the passes below can see every index at once)."""
    out = bt.run(k, df, gate=gate)
    pos = {t: n for n, t in enumerate(df.index)}
    sig = F["sigma"].to_numpy()
    adx = pre["adx"]
    rows = []
    for tr in out["trades"]:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        legs = ows.simulate_live(A, i, tr, st, target)
        p = ps.price(k, df, A, i, tr, legs, s)
        if p is None:
            continue
        exit_px, exit_bar, _ = legs[-1]
        p["side"] = tr["side"]
        p["closed_via"] = ("stop" if abs(exit_px - tr["stop"]) < 1e-6 else
                           "target" if tr.get(target) is not None and abs(exit_px - tr[target]) < 1e-6 else "other")
        p["exit_adx"] = float(adx.iloc[exit_bar])
        p["index"] = k
        rows.append(p)
    rows.sort(key=lambda r: r["when"])
    return rows


def per_index(raw):
    """Today: each index on its own (ccs.sequential_trades_conditional), nothing across indices."""
    kept = []
    for k in INDICES:
        kept += ccs.sequential_trades_conditional([r for r in raw if r["index"] == k],
                                                  base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
                                                  waive_adx_threshold=config.ADX_TREND_THRESHOLD)
    return sorted(kept, key=lambda r: r["when"])


def joint(raw, cap):
    """The same per-index rules walked over all three indices at once, plus: skip a trade while `cap` trades in its
    direction are open on the OTHER indices. cap=None must reproduce per_index() exactly (checked in main)."""
    state = {k: {"open_until": None, "last": {"CE": None, "PE": None}} for k in INDICES}
    kept = []
    for tr in sorted(raw, key=lambda r: (r["when"], INDICES.index(r["index"]))):
        s = state[tr["index"]]
        if s["open_until"] is not None and tr["when"] < s["open_until"]:
            continue
        prior = s["last"][tr["side"]]
        if prior is not None:
            waived = prior["closed_via"] == "target" and prior["exit_adx"] >= config.ADX_TREND_THRESHOLD
            if tr["when"] < prior["close"] + pd.Timedelta(minutes=0 if waived else config.REENTRY_COOLDOWN_MIN):
                continue
        if cap is not None:
            same = sum(1 for o in kept if o["index"] != tr["index"] and o["side"] == tr["side"]
                       and o["when"] <= tr["when"] < o["exit_time"])
            if same >= cap:
                continue
        kept.append(tr)
        s["open_until"] = tr["exit_time"]
        s["last"][tr["side"]] = {"close": tr["exit_time"], "closed_via": tr["closed_via"], "exit_adx": tr["exit_adx"]}
    return kept


def opening_move(df):
    """{date: (first 15-minute candle's close / yesterday's last close) - 1} - known once that candle closes."""
    d = pd.Series(df.index.date, index=df.index)
    first_close = df["Close"].groupby(d.values).first()
    last_close = df["Close"].groupby(d.values).last()
    return (first_close / last_close.shift(1) - 1.0).dropna().to_dict()


def chase_gate(base, df, move, g, cutoff):
    """base AND NOT (a big opening move, this trade in its direction, from a candle starting before `cutoff`)."""
    dates = [t.date() for t in df.index]
    times = [t.time() for t in df.index]

    def gate(i, rec):
        m = move.get(dates[i])
        if m is not None and abs(m) >= g and times[i] < cutoff:
            if (m < 0) == (rec["option_type"] == "PE"):
                return False
        return base(i, rec)
    return gate


def rising_gate(base, pre):
    adx = pre["adx"].to_numpy()

    def gate(i, rec):
        return i > 0 and adx[i] > adx[i - 1] and base(i, rec)
    return gate


def split(rows):
    out = {}
    for p, sel in (("is", [r for r in rows if r["when"] < SPLIT]), ("oos", [r for r in rows if r["when"] >= SPLIT])):
        s = ps.stats(sel) or ows._empty()
        days = pd.Series([r["net"] for r in sel], index=[r["when"].date() for r in sel]).groupby(level=0).sum() \
            if sel else pd.Series(dtype=float)
        s["worst_day"] = float(days.min()) if len(days) else 0.0
        out[p] = s
    return out


def line(name, rows):
    r = split(rows)
    a, b = r["is"], r["oos"]
    return (f" {name:46s} {a['n']:>5} {a['win']:>4.0f}% ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
            f" day ₹{a['worst_day']:>+8,.0f}  | {b['n']:>4} {b['win']:>4.0f}% ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f}"
            f" DD ₹{b['dd']:>8,.0f} day ₹{b['worst_day']:>+8,.0f}")


def verdict(name, rows, base):
    r, b = split(rows), split(base)
    di, do = r["is"]["total"] - b["is"]["total"], r["oos"]["total"] - b["oos"]["total"]
    return (f"   {'KEEP' if di > 0 and do > 0 else 'drop'}  {name:44s} profit: in-sample {di:>+9,.0f}  held-out {do:>+9,.0f}"
            f"   worst drop: {r['is']['dd'] - b['is']['dd']:>+8,.0f} / {r['oos']['dd'] - b['oos']['dd']:>+8,.0f}"
            f"   worst day: {r['is']['worst_day'] - b['is']['worst_day']:>+8,.0f} / {r['oos']['worst_day'] - b['oos']['worst_day']:>+8,.0f}")


_CTX = {}


def _context():
    """Everything a replay needs, loaded once per process (the replays run side by side)."""
    if _CTX:
        return _CTX
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A, pre, st, move = {}, {}, {}, {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
        pre[k] = bt.precompute(df, k)
        st_len, st_mult = config.supertrend_params(k)
        st[k] = ind.supertrend(df, st_len, st_mult)[0].to_numpy()
        move[k] = opening_move(df)
    _CTX.update(hists=hists, F=F, A=A, pre=pre, st=st, move=move,
                target=str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower(),
                live={k: (lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k])) for k in INDICES})
    return _CTX


def _replay(spec):
    """(spec, every index's raw candidates) for one entry rule: ("live",), ("chase", g, cutoff) or ("rising",)."""
    c = _context()
    live, hists = c["live"], c["hists"]
    if spec[0] == "live":
        gates = live
    elif spec[0] == "chase":
        gates = {k: chase_gate(live[k], hists[k], c["move"][k], spec[1], spec[2]) for k in INDICES}
    else:
        gates = {k: rising_gate(live[k], c["pre"][k]) for k in INDICES}
    rows = []
    for k in INDICES:
        rows += raw_candidates(k, hists[k], gates[k], c["A"][k], c["F"][k], c["pre"][k], c["st"][k], c["target"])
    return spec, rows


def main():
    from concurrent.futures import ProcessPoolExecutor
    specs = [("live",)] + [("chase", g, cut) for g in GAPS for cut in CUTOFFS] + [("rising",)]
    print(f"Replaying the live entries under {len(specs)} entry rules, side by side...", flush=True)
    with ProcessPoolExecutor(max_workers=min(len(specs), 8)) as ex:
        got = dict(ex.map(_replay, specs))
    move = _context()["move"]
    raw = got[("live",)]
    base = per_index(raw)
    same = joint(raw, None)
    assert [(r["index"], r["when"]) for r in same] == [(r["index"], r["when"]) for r in base], \
        "joint(cap=None) must reproduce the per-index walk"

    print("=" * 172)
    print(" NIFTY + Bank Nifty + Sensex, per lot, real expiries, after costs. Live entry checks, live exit (2-hour breakeven "
          "priced as it fills), one position per index, cooldown waiver.")
    print(" columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print("-" * 172)
    print(line("today", base))
    results = {}
    print("-" * 172)
    print(" A. one bet, not three: at most N open trades in the same direction across the indices")
    for cap in (1, 2):
        results[f"A cap {cap}"] = joint(raw, cap)
        print(line(f"   at most {cap} at once", results[f"A cap {cap}"]))
    print("-" * 172)
    print(" B. don't chase a big opening move: no entry in its direction before the cutoff")
    for g in GAPS:
        for cut in CUTOFFS:
            name = f"B move {100 * g:.2f}%+, before {cut.strftime('%H:%M')}"
            results[name] = per_index(got[("chase", g, cut)])
            print(line("   " + name[2:], results[name]), flush=True)
    print("-" * 172)
    print(" C. ADX must be rising at entry")
    results["C ADX rising"] = per_index(got[("rising",)])
    print(line("   ADX higher than one candle earlier", results["C ADX rising"]))

    print("\n VERDICT - KEEP only if more profit than today in BOTH periods (worst drop / worst day: minus = better)")
    for name, rows in results.items():
        print(verdict(name, rows, base))

    # How often the situation even comes up: same-direction trades open together, and big opening moves.
    def overlap(rows):
        n = 0
        for r in rows:
            if any(o is not r and o["index"] != r["index"] and o["side"] == r["side"] and o["when"] <= r["when"] < o["exit_time"]
                   for o in rows):
                n += 1
        return n
    print(f"\n HOW OFTEN: {overlap(base)} of {len(base)} of today's trades opened while another index held the same "
          f"direction; days with a 0.5%+ opening move: "
          + ", ".join(f"{k} {sum(abs(v) >= 0.005 for v in move[k].values())} of {len(move[k])}" for k in INDICES))
    tog = [r for r in base if any(o is not r and o["index"] != r["index"] and o["side"] == r["side"]
                                   and o["when"] <= r["when"] < o["exit_time"] for o in base)]
    alone = [r for r in base if r not in tog]
    for label, sel in (("opened alongside a same-direction trade", tog), ("opened alone", alone)):
        s = split(sel)
        print(f"   {label:42s} in-sample {s['is']['n']:>4} trades ₹{s['is']['avg'] if s['is']['n'] else 0:>+7,.0f}/trade"
              f"   held-out {s['oos']['n']:>4} trades ₹{s['oos']['avg'] if s['oos']['n'] else 0:>+7,.0f}/trade")


if __name__ == "__main__":
    main()
