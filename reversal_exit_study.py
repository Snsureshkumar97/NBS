#!/usr/bin/env python3
"""
reversal_exit_study.py — does EARLY_EXIT_ON_REVERSAL (tickets.py) earn its keep?
================================================================================
The user, 27 Sep 2026: "give the signal tool also its own exit time like AI trades" - built as
EARLY_EXIT_ON_REVERSAL in config.py (off by default): while a ticket is open, the OPPOSITE direction
has to hold for the same SIGNAL_CONFIRM_SECONDS / SIGNAL_CONFIRM_TICKS a fresh entry itself needs
before the ticket closes early. This runs that exit through pro_study.py's own pricing (real expiries,
Zerodha's costs, the same rupees-after-costs stats() and the same in-sample/held-out bar) before it is
ever considered for real trades, per this project's own standing practice.

THE ENTRIES ARE TODAY'S ACTUAL LIVE GATES, NOT PRO_STUDY.PY'S OWN "LIVE RULES" ROW
    pro_study.py's own "LIVE RULES (OR break)" label predates several changes since adopted (skills_
    study.py's RSI-divergence filter and the trend-day room 15 Sep 2026; REGIME_OR_BREAK switched off
    entirely 16 Sep 2026) and so no longer describes what tickets.py actually enters on. Rather than
    add yet another hand-copied approximation, this calls tickets.py's own gate methods directly -
    _regime_hold, _divergence_hold, _reward_hold, _spread_hold - all four confirmed stateless (only
    `rec` and config, no book/account state), so this is the real, current, shipped entry logic, not a
    snapshot of it: it keeps describing "live" even after config.py changes again. The three gates that
    ARE stateful (already-ticketed-today, the ticket gap, same-strike-today) are not replayed - neither
    pro_study.py nor regime_study.py ever have either, relying on bt.run()'s own simplified trade
    selection (min_gap_bars) instead; that gap is inherited here, not introduced by this file.

THE ONE APPROXIMATION THIS MAKES, AND ITS DIRECTION
    A backtest only has the 15-minute bar; the live confirmation clock runs on the signal recomputing up
    to once a SECOND (config.LIVE_ANALYSIS_MS). So rather than simulate a sub-bar clock this asks, at
    every bar from entry onward, what signal_engine.build_recommendation() - the exact function the live
    tool calls - would say RIGHT THEN, unconstrained by any entry gate, and treats a bar whose answer is
    a clear OPPOSITE side (never a neutral one) as having already satisfied the live confirmation the
    moment that bar's close is observed.

    This is optimistic, and in one specific way this file cannot measure: nothing here can tell whether
    that reading had genuinely HELD for the full 120 seconds / 4 readings by the time the bar closed, or
    only turned in the last few seconds of a 900-second bar - which live would not yet count as sustained.
    A technical indicator built from EMA/MACD/RSI/VWAP/ADX does not usually flip and flip back inside one
    candle, so a bar that closes reading a clear opposite side is more likely than not to have meant it
    for a while - but "more likely than not" is not "measured", and this file does not claim it is. The
    result below should be read as: AT LEAST this many bars saw a genuine opposite reading at their
    close; live, with the real sub-bar clock, would fire on some equal-or-smaller subset of them - so a
    KEEP verdict here is not proof live will do exactly this well, and a DROP is not proof it would not.

    Target and stop are always checked first, exactly as tickets.py's own _check_price does - the two
    never race for the same bar.

WHAT THIS DOES NOT DO
    This is an EXIT study, laid over pro_study.py's entries unchanged - it says nothing about whether
    a different entry would pair better with this exit, and nothing changes anywhere else. Kept only if
    it beats today's live exit (hold to T2/stop) BOTH in-sample and on the held-out final year, in the
    same rupees-after-costs terms pro_study.py itself uses.

    python3 reversal_exit_study.py
"""
import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs
import signal_engine as se
import tickets

INDICES = ps.INDICES
SPLIT = ps.SPLIT
IST = ps.IST
_SELF = object()          # tickets.TicketBook's gate methods are stateless (verified below); a real
                          # instance is never needed, only something to bind as `self` and never touch


def _assert_gates_are_stateless():
    """Fails loudly, at import time, if a future edit makes any of these methods touch self/book state
    - better than silently producing a wrong-but-plausible number."""
    rec = {"index": "NIFTY", "option_type": "CE", "spot": 100.0, "risk_points": 10.0,
           "index_targets": [110.0, 120.0, 130.0], "opening_range": {"ready": False, "high": None, "low": None},
           "spread": None, "candles": None}
    for fn, args in ((tickets.TicketBook._regime_hold, (rec,)), (tickets.TicketBook._divergence_hold, (rec,)),
                     (tickets.TicketBook._reward_hold, ("NIFTY", rec)), (tickets.TicketBook._spread_hold, (rec,))):
        fn(_SELF, *args)               # AttributeError here means this file's premise no longer holds


_assert_gates_are_stateless()


# ---------------------------------------------------------------- today's real entry gates
def live_gate(index_key, i, rec, df):
    """May this reading be entered - tickets.py's own _regime_hold / _divergence_hold / _reward_hold /
    _spread_hold, called directly (see the module docstring for why). `rec["candles"]` and
    `rec["opening_range"]` are set here exactly as feeds.py sets them live, from candles up to and
    including bar i - build_recommendation() itself does not set either."""
    rec = dict(rec)
    rec["candles"] = df.iloc[:i + 1]
    rec["opening_range"] = se.opening_range(rec["candles"])
    return (tickets.TicketBook._regime_hold(_SELF, rec) is None
            and tickets.TicketBook._divergence_hold(_SELF, rec) is None
            and tickets.TicketBook._reward_hold(_SELF, index_key, rec) is None
            and tickets.TicketBook._spread_hold(_SELF, rec) is None)


# ---------------------------------------------------------------- the raw signal, every bar
def bar_bias(index_key, df):
    """(bias array, option_type array) at EVERY bar from warmup on - build_recommendation()'s read,
    unconstrained by min_gap_bars or an open trade: what the live tool would say if asked right then.
    A NEUTRAL bar stores None in both. This is the one extra pass pro_study.py's own bt.run() does not
    give you: it skips computing a reading near a trade it already took, because it only needs entries."""
    pre = bt.precompute(df, index_key)
    step = config.INSTRUMENTS[index_key]["strike_step"]
    no_chain = se.compute_option_chain_signal(None)
    warmup = max(config.EMA_SLOW, config.ADX_LENGTH * 2, 60) + 5
    n = len(df)
    bias = np.full(n, None, dtype=object)
    opt = np.full(n, None, dtype=object)
    typical_s = pre["typical"]
    atr_s = pre["atr"]
    for i in range(warmup, n - 1):
        if not np.isfinite(typical_s.iloc[i]) or not np.isfinite(atr_s.iloc[i]) or atr_s.iloc[i] <= 0:
            continue
        tech = bt.tech_at(df, pre, i)
        stamp = df.index[i]
        now = stamp.to_pydatetime()
        if now.tzinfo is None:
            now = now.replace(tzinfo=bt.IST)
        reach = se.compute_reachability(
            tech["last_close"], no_chain, df, now, adx=tech["adx"],
            range_stats=(float(typical_s.iloc[i]), float(pre["used_today"].iloc[i])),
            index_key=index_key,
            day_extremes=(float(pre["day_high"].iloc[i]), float(pre["day_low"].iloc[i])))
        rec = se.build_recommendation(index_key, tech, no_chain, step, reach=reach)
        if rec["bias"] != "NEUTRAL":
            bias[i] = rec["bias"]
            opt[i] = rec["option_type"]
    return bias, opt


# ---------------------------------------------------------------- the exit itself
def simulate_reversal(A, opt_arr, i, tr, hold_bars=26, square_off=True):
    """Legs of one trade: target and stop checked exactly as pro_study.simulate()'s default (live)
    exit checks them - target/stop only, no breakeven-after-T1 or half-at-T1, matching the baseline
    this is measured against - PLUS: once the bar's own reading (opt_arr) is the opposite side, the
    trade closes there, unless target or stop already closed it on this same bar, checked first."""
    target = str(getattr(config, "EXIT_AT_TARGET", "T3") or "T3").lower()
    if target not in ("t1", "t2", "t3"):
        target = "t3"
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    opposite = "PE" if ce else "CE"
    stop, legs, rem = tr["stop"], [], 1.0
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            legs.append((stop, j, rem)); return legs
        tgt = tr[target]
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            legs.append((tgt, j, rem)); return legs
        if opt_arr[j] == opposite:
            legs.append((cl[j], j, rem)); return legs
        if square_off and end[j]:
            legs.append((cl[j], j, rem)); return legs
    last = min(i + hold_bars, n - 1)
    legs.append((cl[last], last, rem))
    return legs


def main():
    print("Loading history (shared cache with pro_study.py / backtest_intraday.py)...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    import math
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
    base_gate = {k: (lambda i, rec, k=k: live_gate(k, i, rec, hists[k])) for k in INDICES}

    print("Reading the raw signal at every bar (the extra pass this needs beyond pro_study.py)...")
    opt_arrs = {}
    for k in INDICES:
        print(f"  {k}...")
        _, opt_arrs[k] = bar_bias(k, hists[k])

    def run_one(reversal):
        """`reversal`: False is today's live exit (pro_study.simulate()'s default - target/stop only,
        no breakeven-after-T1 or half-at-T1) on today's real entries; True lays the early-exit-on-
        reversal check on top of the identical target/stop, on the identical entries - only the exit
        differs between the two rows this produces."""
        rows = []
        for k in INDICES:
            df = hists[k]
            res = bt.run(k, df, gate=base_gate[k])
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in res["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = (simulate_reversal(A[k], opt_arrs[k], i, tr) if reversal
                       else ps.simulate(A[k], i, tr))
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}, rows

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:34s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    hdr = (f" {'variant':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print("=" * 132); print(" All per lot, pooled across NIFTY / BANKNIFTY / SENSEX, real expiries, after costs")
    print(" Entries: today's real gates - _regime_hold, _divergence_hold, _reward_hold, _spread_hold, called")
    print(" directly - unchanged. Only the EXIT differs below.")
    print("=" * 132); print(hdr); print("-" * 132)

    base, base_rows = run_one(False)
    print(line("hold to T2/stop (live, today)", base))

    rev, rev_rows = run_one(True)
    print(line("EARLY_EXIT_ON_REVERSAL", rev))
    # how many trades the reversal exit actually cut short, vs the baseline's own exit bar
    base_exit = {(r["index"], r["when"]): r["exit_time"] for r in base_rows}
    cut = sum(1 for r in rev_rows if base_exit.get((r["index"], r["when"])) and r["exit_time"] < base_exit[(r["index"], r["when"])])
    print(f"   ({cut} of {len(rev_rows)} trades exited earlier than the live hold-to-target/stop would have)")
    print("-" * 132)

    print("\n VERDICT — kept only if better than the live exit both in-sample and out-of-sample (pro_study.py's own bar)")
    di = rev["is"]["total"] - base["is"]["total"]
    do = rev["oos"]["total"] - base["oos"]["total"]
    dd = rev["oos"]["dd"] - base["oos"]["dd"]
    keep = di > 0 and do > 0
    print(f"   {'KEEP ' if keep else 'DROP '} EARLY_EXIT_ON_REVERSAL   in-sample {di:>+10,.0f}   "
          f"out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    verdict_line = ("This clears pro_study.py's own bar to be adopted for real trades." if keep
                    else "This does NOT clear the bar; EARLY_EXIT_ON_REVERSAL stays off.")
    print(f"\n   {verdict_line}")
    return {"live": base, "reversal": rev}


if __name__ == "__main__":
    main()
