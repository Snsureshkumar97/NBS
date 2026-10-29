#!/usr/bin/env python3
"""
far_expiry_check.py — is the model right about options 14+ days out? Bank Nifty and Midcap, on real prices
================================================================================
The user, 29 Oct 2026: "yes start the far expiry check". The six-year backtest's best result rests on options at
least 14 days from expiry (expiry_study.py, pricing.MIN_DTE 14: the user's setup +954,607 / +371,902 a lot, against
+160,000 / +152,775 at the nearest expiry) - priced by the model (pricing.py), whose 14+ day cell has had little real
evidence. The paper test of it runs from 29 Oct, judged mid-November. But the recorder has kept Bank Nifty's and
Midcap's next-month contracts - 20 to 50 days out - since 10 Sep: on those two the model can be checked now.

FIXED BEFORE ANY RESULT WAS SEEN (29 Oct 2026). debit_spread_study's trades (honest_retest.py's harness) on Bank
Nifty and Midcap, 10 Sep - 26 Oct 2026, of the user's setup (the rating with yesterday's levels + the big candle;
Midcap the rating with the levels), the same one trade a day, and the rating alone. Each trade buys the at-the-money
option of the first recorded expiry at least 14 calendar days out (the live far setting, config.FAR_EXPIRY_DAYS),
priced on that same contract at the same minutes twice:
  REAL   the recording, from a candle at most 5 minutes old (debit_spread_study.fresh)
  MODEL  pricing.premium at that contract's expiry, the day's sigma (what the six-year backtest does)
per lot after every charge and 0.25% slippage a side. Also the same trades at the nearest expiry, real and model.
The model's error = MODEL - REAL per trade, over every distinct trade of the three systems (a price check, so the
more trades the better). CREDIBLE only if, on each index, that average error is under HALF the six-year profit a
trade the backtest credits to 14+ days there; otherwise the far-expiry result may be mostly the model.

RESULT (29 Oct 2026; far_expiry_check_results.txt). NOT CREDIBLE - the 14+ day result was mostly a model artifact.
  BANK NIFTY, 62 trades on the real far contracts (14-41 days out, median 32): the model is too rosy by +296 a trade
  (se 98) against half the six-year +391 a trade (+195); at the nearest expiry, the same trades, +48 (se 88) - fine.
  Real: the same 62 trades made about the same far as near (-38,062 against -39,262). MIDCAP: only 21 of 63 trades
  have a fresh far price (its far contracts barely trade): -157 a trade, se 654 - says nothing.
  WHY (scratchpad far_diag.py): pricing.SIGMA_RATIO's 14+ day cell rises through the day (0.872, 0.894, 0.914, 0.919)
  more than a far option's time value falls in one session, so the model's far option GAINS from the clock alone - a
  35-day Bank Nifty at-the-money call, the index unchanged: 820 at 09:45, 837 at 11:45, 854 at 14:45 (+1,009 a lot).
  The error grows with the time held: under an hour -123 a trade, 1-2 hours +449, 2-4 +605, 4+ +622.
  WHAT-IF (scratchpad far_whatif.py: the cell flattened to its day average, 0.8998): the user's setup at 14+ days
  +954,607 / +371,902 -> +2,052 / +86,852 (nearest, which uses the cell early in Bank Nifty's and Midcap's months,
  +160,000 / +152,775 -> +74,666 / +89,080); one a day 14+ days +54,762 / +79,515, nearest +133,645 / +81,758; the
  rating alone loses both ways. Flattened, Bank Nifty's far error is -141 a trade (se 95): slightly cautious - the
  truth lies between, much nearer the flattened figures. 14+ days is no better than the nearest expiry.

    HOME=<six-year history> /usr/bin/python3 far_expiry_check.py OPTION_HISTORY_DIR
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debit_spread_study as dss

KEYS = ("BANKNIFTY", "MIDCPNIFTY")
FAR_DAYS = 14
SLIP = dss.SLIP


def far_expiry(expiries, d):
    """The first expiry at least FAR_DAYS calendar days after day d (ISO strings), or None."""
    for e in sorted(expiries):
        if (dt.date.fromisoformat(e) - d).days >= FAR_DAYS:
            return e
    return None


def exit_minute(r, P):
    """The minute the trade left - as real_price_check.real_net."""
    t_out = pd.Timestamp(r["exit_time"])
    exit_t = t_out - pd.Timedelta(minutes=1)
    if r["how"] == "level":
        idx = P.index(r["index"], pd.Timestamp(r["when"]).date().isoformat())
        if idx is not None and len(idx):
            win = idx[(idx.index >= t_out - pd.Timedelta(minutes=15)) & (idx.index < t_out)]
            hit = win[(win["low"] <= r["exit_spot"]) & (win["high"] >= r["exit_spot"])]
            if len(hit):
                exit_t = hit.index[0]
            elif len(win):
                exit_t = win.index[0]
    return exit_t


def both(r, P, far=True):
    """(real net, model net, real entry price, model entry price, expiry) on one contract, or None."""
    import config
    import pricing
    import regime_study as rs
    k = r["index"]
    meta = config.INSTRUMENTS[k]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    t_in, t_out = pd.Timestamp(r["when"]), pd.Timestamp(r["exit_time"])
    d = t_in.date()
    exps = [e for e in P.expiries(k, d.isoformat()) if e >= d.isoformat()]
    exp = far_expiry(exps, d) if far else (exps[0] if exps else None)
    if exp is None:
        return None
    call = r["side"] == "CE"
    K = round(r["entry_spot"] / step) * step
    opt = P.option(k, d.isoformat(), exp, K, r["side"])
    p0, p1 = dss.fresh(opt, t_in, "open"), dss.fresh(opt, exit_minute(r, P), "close")
    if p0 is None or p1 is None or p0 <= 0.5:
        return None
    e = dt.date.fromisoformat(exp)
    q0 = pricing.premium(r["entry_spot"], K, e, t_in, r["sigma"], call)
    q1 = pricing.premium(r["exit_spot"], K, e, t_out, r["sigma"], call)
    if q0 <= 0.5:
        return None
    return (rs.net_rupees(p0, p1, qty, exch, SLIP), rs.net_rupees(q0, q1, qty, exch, SLIP), p0, q0, exp)


def systems(got, k):
    import oneperday_study as opd
    import order_block_site_study as obs
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    tl, bc, tv = (s(got[(k, x)][0]) for x in ("TVL", "BC", "TV"))
    setup = s(obs.slot(tl, bc)) if k != "MIDCPNIFTY" else tl
    return {"your setup": setup, "your setup, one a day": s(opd.first_per_day(setup)), "the rating alone": tv}


def err_line(pairs):
    """pairs: [(real net, model net, real price, model price)] -> one line of the model's error."""
    e = np.array([m - r for r, m, _, _ in pairs])
    ratio = np.median([q / p for _, _, p, q in pairs])
    se = e.std(ddof=1) / np.sqrt(len(e)) if len(e) > 1 else float("nan")
    return (f"{len(e):4d} trades  model - real {e.mean():+8,.0f} a trade (se {se:,.0f})  real {sum(r for r, *_ in pairs):+10,.0f}"
            f"  model {sum(m for _, m, *_ in pairs):+10,.0f}  model's entry price / real: median {ratio:.2f}"), e.mean()


def main():
    import multiprocessing as mp
    import live_vs_backtest as lv
    import real_price_check as rpc
    P = lv.Prices(sys.argv[1])
    jobs = [(k, sy) for k in KEYS for sy in ("TV", "TVL", "BC")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(dss._job, jobs, chunksize=1))
    inside = lambda r: rpc.SINCE <= pd.Timestamp(r["when"]) < rpc.UNTIL
    print(f"REAL PRICES {rpc.SINCE.date()} to {(rpc.UNTIL - pd.Timedelta(days=1)).date()} against the MODEL, the same "
          f"contract at the same minutes, per lot after charges and 0.25% slippage a side")
    for k in KEYS:
        S = systems(got, k)
        print("=" * 150)
        six = S["your setup"]
        far6 = [r["m14"]["NAKED"] for r in six if r["m14"]["NAKED"] is not None]
        near6 = [r["m0"]["NAKED"] for r in six if r["m0"]["NAKED"] is not None]
        per = np.mean(far6)
        print(f"{k}: the six-year backtest credits your setup {per:+,.0f} a trade at 14+ days ({len(far6)} trades, "
              f"{sum(far6):+,.0f}), {np.mean(near6):+,.0f} at the nearest expiry")
        seen, distinct = set(), []
        for rows in S.values():
            for r in rows:
                key = (pd.Timestamp(r["when"]), r["side"])
                if inside(r) and key not in seen:
                    seen.add(key)
                    distinct.append(r)
        for far, label in ((True, "14+ DAYS"), (False, "NEAREST")):
            got_ = [both(r, P, far) for r in distinct]
            pairs = [g[:4] for g in got_ if g]
            text, mean_err = err_line(pairs)
            print(f"   {label:9s} MODEL CHECK, {len(distinct)} distinct trades: {text}")
            if far:
                verdict = "CREDIBLE" if mean_err < per / 2 else "NOT CREDIBLE"
                print(f"   {'':9s} -> {verdict}: the model's error {mean_err:+,.0f} a trade against half the six-year "
                      f"profit a trade, {per / 2:+,.0f}")
                exps = pd.Series([g[4] for g in got_ if g])
                days = pd.Series([(dt.date.fromisoformat(g[4]) - pd.Timestamp(r["when"]).date()).days
                                  for r, g in zip(distinct, got_) if g])
                print(f"   {'':9s}    contracts: {', '.join(f'{e} x{n}' for e, n in exps.value_counts().sort_index().items())}"
                      f"; days to expiry at entry {days.min()}-{days.max()} (median {days.median():.0f})")
        for name, rows in S.items():
            win = [r for r in rows if inside(r)]
            cells = []
            for far, label in ((True, "14+ days"), (False, "nearest")):
                g = [both(r, P, far) for r in win]
                ok = [x for x in g if x]
                cells.append(f"{label}: real {sum(x[0] for x in ok):+9,.0f} model {sum(x[1] for x in ok):+9,.0f} "
                             f"({len(ok)} priced, {100 * sum(x[0] > 0 for x in ok) / max(len(ok), 1):.0f}% won real)")
            print(f"   {name:24s} {len(win):3d} trades | " + " | ".join(cells))


if __name__ == "__main__":
    main()
